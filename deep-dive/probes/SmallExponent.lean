-- SPDX-License-Identifier: MIT

import Lean.Meta.Diagnostics

/-!
A small, stock-kernel control for the recursion used by `npowRec`.

`PowTrace` preserves the multiplication tree instead of performing algebra.
The recurrence examples below are proved by `rfl`, so the kernel must reduce
the actual `npowRec` definition rather than use an exponentiation lemma or
tactic. The arbitrary-`n` tree-size theorem is proved separately by induction.
-/

inductive PowTrace where
  | one
  | atom
  | mul (left right : PowTrace)

instance : One PowTrace := ⟨.one⟩
instance : Mul PowTrace := ⟨.mul⟩
instance : Pow PowTrace Nat := ⟨fun a n => npowRec n a⟩

def PowTrace.mulNodes : PowTrace → Nat
  | .one | .atom => 0
  | .mul left right => left.mulNodes + right.mulNodes + 1

example : PowTrace.atom ^ 0 = PowTrace.one := rfl
example (n : Nat) :
    PowTrace.atom ^ (n + 1) = PowTrace.atom ^ n * PowTrace.atom := rfl

theorem PowTrace.mulNodes_pow_atom (n : Nat) :
    (PowTrace.atom ^ n).mulNodes = n := by
  induction n with
  | zero => rfl
  | succ n ih =>
      change (PowTrace.atom ^ n).mulNodes + 1 = n + 1
      rw [ih]

def koalaFieldSize : Nat := 2130706433
def koalaExponent : Nat := koalaFieldSize ^ 6
def fortyGiBBits : Nat := 40 * 1024 ^ 3 * 8

theorem koalaExponent_exceeds_fortyGiBBits : fortyGiBBits < koalaExponent := by
  decide

#eval IO.println "EXPONENT 4"
set_option diagnostics true in
set_option diagnostics.threshold 0 in
example : (PowTrace.atom ^ 4).mulNodes = 4 := rfl

#eval IO.println "EXPONENT 8"
set_option diagnostics true in
set_option diagnostics.threshold 0 in
example : (PowTrace.atom ^ 8).mulNodes = 8 := rfl

#eval IO.println "EXPONENT 16"
set_option diagnostics true in
set_option diagnostics.threshold 0 in
example : (PowTrace.atom ^ 16).mulNodes = 16 := rfl

#eval IO.println "EXPANDED 4"
#reduce PowTrace.atom ^ 4

#eval IO.println "LARGE LOWER BOUND"
#eval IO.println s!"fieldSize = {koalaFieldSize}"
#eval IO.println s!"fieldSize^6 nodes = {koalaExponent}"
#eval IO.println s!"40 GiB bits = {fortyGiBBits}"
