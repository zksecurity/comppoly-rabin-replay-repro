import Export.Parse
import Lean.Replay

open Lean

def parseExport (path : System.FilePath) : IO Export.ExportedEnv :=
  IO.FS.withFile path .read fun handle =>
    Export.parseStream (IO.FS.Stream.ofHandle handle)

partial def instantiateLets : Expr → Expr
  | .letE _ _ value body _ => instantiateLets (body.instantiate1 value)
  | e => e

partial def collectMaximalEqMpr (e : Expr) (inside : Bool := false) : Array Expr :=
  let isMpr := e.getAppFn.constName? == some ``Eq.mpr
  let here := if isMpr && !inside then #[e] else #[]
  let inside := inside || isMpr
  let children := match e with
    | .app fn arg => collectMaximalEqMpr fn inside ++ collectMaximalEqMpr arg inside
    | .lam _ type body _ | .forallE _ type body _ =>
      collectMaximalEqMpr type inside ++ collectMaximalEqMpr body inside
    | .letE _ type value body _ =>
      collectMaximalEqMpr type inside ++ collectMaximalEqMpr value inside ++
        collectMaximalEqMpr body inside
    | .mdata _ body | .proj _ _ body => collectMaximalEqMpr body inside
    | _ => #[]
  here ++ children

def main (args : List String) : IO Unit := do
  initSearchPath (← findSysroot)
  let [path] := args
    | throw <| .userError "usage: SubtermCheck EXPORT.ndjson"
  let exported ← parseExport path
  let target := `KoalaBear.sexticPoly_irreducible
  let some targetInfo := exported.constMap[target]?
    | throw <| .userError s!"missing target {target}"
  let value := match targetInfo with
    | .thmInfo info => info.value
    | _ => panic! "target is not a theorem"
  let mut constMap := exported.constMap
  constMap := constMap.erase `Quot.mk |>.erase `Quot.lift |>.erase `Quot.ind
  constMap := constMap.erase `ReplayDemo.target |>.erase target
  let env ← (← mkEmptyEnvironment).replay constMap
  let value := instantiateLets value
  let transports := collectMaximalEqMpr value
  IO.println s!"found {transports.size} maximal Eq.mpr applications"
  for h : i in [:transports.size] do
    let transport := transports[i]
    let head := transport.getAppFn
    let args := transport.getAppArgs
    IO.println s!"transport[{i}] has {args.size} application arguments"
    for h : j in [:args.size] do
      let partialApp := mkAppN head args[:j+1]
      let prefixStart ← IO.monoMsNow
      match Kernel.check env {} partialApp with
      | .ok _ =>
        IO.println s!"transport[{i}].prefix[{j+1}] ACCEPTED in {(← IO.monoMsNow) - prefixStart} ms"
      | .error ex =>
        let msg ← ex.toMessageData {} |>.toString
        IO.println s!"transport[{i}].prefix[{j+1}] FAILED in {(← IO.monoMsNow) - prefixStart} ms: {msg}"
    let start ← IO.monoMsNow
    match Kernel.check env {} transport with
    | .ok type =>
      IO.println s!"transport[{i}] ACCEPTED in {(← IO.monoMsNow) - start} ms; type depth={type.approxDepth}"
    | .error ex =>
      let msg ← ex.toMessageData {} |>.toString
      IO.println s!"transport[{i}] FAILED in {(← IO.monoMsNow) - start} ms: {msg}"
