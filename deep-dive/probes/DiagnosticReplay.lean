import Export.Parse
import Lean.Replay

open Lean

def parseExport (path : System.FilePath) : IO Export.ExportedEnv :=
  IO.FS.withFile path .read fun handle =>
    Export.parseStream (IO.FS.Stream.ofHandle handle)

def reportDiagnostics (env : Environment) : IO Unit := do
  let counter := (Kernel.getDiagnostics env).unfoldCounter
  let mut total := 0
  let mut distinct := 0
  for (name, count) in counter do
    distinct := distinct + 1
    total := total + count
    IO.println s!"UNFOLD {count} {name}"
  IO.println s!"kernel unfolded {distinct} distinct declarations"
  IO.println s!"kernel unfold events: {total}"

def main (args : List String) : IO Unit := do
  initSearchPath (← findSysroot)
  let [path] := args
    | throw <| .userError "usage: DiagnosticReplay EXPORT.ndjson"
  let exported ← parseExport path
  IO.println s!"parsed {exported.constMap.size} declarations"
  let target := `KoalaBear.sexticPoly_irreducible
  let some targetInfo := exported.constMap[target]?
    | throw <| .userError s!"missing target {target}"
  let mut constMap := exported.constMap
  constMap := constMap.erase `Quot.mk |>.erase `Quot.lift |>.erase `Quot.ind
  constMap := constMap.erase `ReplayDemo.target
  constMap := constMap.erase target
  let start ← IO.monoMsNow
  IO.println "replaying dependencies"
  let env ← (← mkEmptyEnvironment).replay constMap
  IO.println s!"dependencies accepted in {(← IO.monoMsNow) - start} ms"
  let env := Kernel.enableDiag (Kernel.resetDiag env) true
  let targetMap := ({} : Std.HashMap Name ConstantInfo).insert target targetInfo
  let targetStart ← IO.monoMsNow
  IO.println s!"replaying target {target} with kernel diagnostics enabled"
  try
    let env ← env.replay targetMap
    IO.println s!"target accepted in {(← IO.monoMsNow) - targetStart} ms"
    reportDiagnostics env
  catch ex =>
    IO.println s!"target failed in {(← IO.monoMsNow) - targetStart} ms: {ex}"
    reportDiagnostics env
    throw ex
