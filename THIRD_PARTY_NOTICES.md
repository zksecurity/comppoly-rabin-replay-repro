# Third-party notices

The reproduction fetches exact revisions of these projects while building its
container images:

- CompPoly, Copyright (c) its contributors, Apache License 2.0;
- Comparator, Copyright (c) its contributors, Apache License 2.0;
- lean4export, Copyright (c) its contributors, Apache License 2.0;
- Lean 4 and Mathlib, Copyright (c) their contributors, Apache License 2.0; and
- Elan, Copyright (c) its contributors, MIT License or Apache License 2.0.

Their source checkouts retain their upstream license files in the generated
container image. No third-party binary is committed to this repository.

The relevant upstream sources and pinned revisions are:

- [CompPoly](https://github.com/zksecurity/CompPoly), revisions
  `6133f9f796707c438d0a614f97dc218ae976ab8f` and
  `641694629e4557520a1539b272ec338c9f3044c7`;
- [Verified-zkEVM/CompPoly](https://github.com/Verified-zkEVM/CompPoly),
  revisions `32a0c29e41225e8cec2a2e1eab1dfab64f026aa0` and
  `7480a691ff87d178f0d0afd45454d8400e39e268`;
- [Comparator](https://github.com/leanprover/comparator), revision
  `51491237b1d2f96cca203af9c34bced6fe38e0d8`;
- [lean4export](https://github.com/leanprover/lean4export), revision
  `af5aa64bb914c3c2c781f378088dbd38acf4f804`;
- [Lean 4](https://github.com/leanprover/lean4), revision
  `f3b06c705e6c85f5314019d5d3baab0fec5b580c` (Lean 4.32.2); and
- [Mathlib](https://github.com/leanprover-community/mathlib4), revision
  `905b95818eb32af7874a58b427f50c1711a5e96c` in the Lean 4.32.2 suite.

The `deep-dive` directory commits an instrumentation patch against
`src/kernel/environment.cpp`, `src/kernel/equiv_manager.{h,cpp}`, and
`src/kernel/type_checker.{h,cpp}` in the pinned Lean 4 source. It also commits a
CompPoly-derived transport fixture and two patches against
`CompPoly/Data/Polynomial/RabinCertificate.lean` and
`CompPoly/Fields/KoalaBear/Ext6/SexticIrreducible.lean`. These derivative files
are distributed under Apache License 2.0; see [`LICENSES`](LICENSES/README.md)
for the per-file mapping and full text.

The pinned Ubuntu base image contains additional software distributed under
its respective package licenses.
