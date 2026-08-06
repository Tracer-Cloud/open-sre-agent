# Five-step recoverable random-total workflow

State is stored in `state.json` and updated atomically after every step.

For each numbered step 1–5:
1. Load the durable state and determine the next unfinished step.
2. Generate one random integer in the inclusive range 1–100.
3. Add it to the persisted running total.
4. Atomically persist the number, updated total, and completed step number.
5. On restart after a crash, reload `state.json` and resume at the first missing step; completed steps are never regenerated or added twice.

The final state includes all five generated values, the total, and `completed: true`.
