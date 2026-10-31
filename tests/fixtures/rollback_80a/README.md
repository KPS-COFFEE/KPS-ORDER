# Historical refusal witness

These two files preserve the exact prior overlay from the last production source
`b528f14c5ea93e32a15a5c37f43f7e93477136e8`. That older fallback was based on
`80a1d727f6695c4ce3685e726ab5b34674e1cfa0` and deliberately refused newer cash-bag,
shared-closure, clarification, bank and duplicate/partial-return state.

They are test inputs only. The runtime fallback now uses b528 with an explicit,
hash-bound compatibility overlay and must pass its own exact-image contracts.
The historical guard's original SHA-256 is retained in the manifest and asserted
in the test; no unsafe older writer is made eligible by moving this fixture.
