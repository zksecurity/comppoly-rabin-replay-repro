import Lean
open Lean
def target : Name := `KoalaBear.sexticPoly_irreducible
partial def collect (env : Environment) (todo : List Name) (acc : NameSet) : NameSet :=
  match todo with
  | [] => acc
  | n :: rest =>
    if acc.contains n then collect env rest acc
    else match env.find? n with
      | none => collect env rest acc
      | some ci => collect env (ci.getUsedConstantsAsSet.toList ++ rest) (acc.insert n)
def main : IO Unit := do
  initSearchPath (← findSysroot)
  let env ← importModules #[{ module := `CompPoly.Fields.KoalaBear.Ext6.SexticIrreducible }] {}
  let names := collect env [target] {}
  let mut m : Std.HashMap Name ConstantInfo := {}
  for n in names do
    if let some ci := env.find? n then
      if !ci.isUnsafe && !ci.isPartial then m := m.insert n ci
  let e ← mkEmptyEnvironment
  discard <| e.replay m
  IO.println "REPLAY OK"
