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

def onlyTransport (exported : Export.ExportedEnv) : IO Expr := do
  let target := `KoalaBear.sexticPoly_irreducible
  let some targetInfo := exported.constMap[target]?
    | throw <| .userError s!"missing target {target}"
  let value := match targetInfo with
    | .thmInfo info => instantiateLets info.value
    | _ => panic! "target is not a theorem"
  let transports := collectMaximalEqMpr value
  if h : transports.size = 1 then
    return transports[0]
  else
    throw <| .userError s!"expected one maximal Eq.mpr, found {transports.size}"

def main (args : List String) : IO Unit := do
  initSearchPath (← findSysroot)
  let [leftPath, rightPath] := args
    | throw <| .userError "usage: CompareTransport LEFT.ndjson RIGHT.ndjson"
  let left ← onlyTransport (← parseExport leftPath)
  let right ← onlyTransport (← parseExport rightPath)
  unless left == right do
    throw <| .userError "maximal Eq.mpr expressions differ structurally"
  IO.println s!"maximal Eq.mpr expressions are structurally equal; depth={left.approxDepth} hash={hash left}"
