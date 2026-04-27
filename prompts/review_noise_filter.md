# Review noise filter

Rules that suppress low-signal findings in code review. Apply these alongside the standard review process. Each rule should answer: "if this ships unfixed, what concretely goes wrong?" If you can't articulate the harm in one sentence using concrete language about real users, maintainers, or operators, don't flag it.

## General principles

1. **Quantify in deployment context.** Performance, accessibility, and security findings should reference the actual users, network, and threat model — not generic web-scale heuristics. A 200 KB bundle on an authenticated returning-user tool is not the same finding as 200 KB on a marketing page. State the concrete impact in the user's environment, or don't flag it.

2. **Don't flag speculative future state.** "Could become a problem if X is later widened, exposed, or changed" is not a finding. Flag what's wrong now, not what might be wrong if the code were different.

3. **Don't flag duplicated coverage.** If a behavior is already tested through a higher-level path, additional unit tests on its helpers add no signal — they test the implementation, not the behavior. Likewise, if a field uses the same validator as another already-tested field, the second test covers the same code.

4. **Don't refactor for refactor's sake.** Equivalence-preserving changes — parametrizing identical tests, extracting a 3-line helper, renaming a constant — require a justification beyond aesthetics. Articulate the maintenance burden being reduced. If the only argument is "it would read nicer," don't flag it.

5. **Honor stated tradeoffs.** If the PR description documents a known cost being accepted ("acceptable for X; future optimization", "deferring Y to follow-up"), do not relitigate it. The author already weighed the tradeoff.

## Concrete don't-flag patterns

These are specific cases observed in the wild. Add new ones as patterns recur.

- **Defense-in-depth fixes for inputs that are currently hardcoded.**
  Example: Suggesting HTML escaping or URL sanitization on a value that is, in the current code, a compile-time constant. Until the input is widened to accept user content, the fix protects against nothing.

- **Direct unit tests for helpers that are already exercised through their callers' tests.**
  Example: A small validation helper is called by three route handlers, each of which has integration tests that exercise the helper indirectly. Demanding a separate unit test class for the helper duplicates coverage.

- **Test "symmetry" — additional tests for fields that share a validator with an already-tested field.**
  Example: `field_a` and `field_b` use the same `strip_and_reject_blank` validator. `test_blank_field_a_raises` exists; demanding `test_blank_field_b_raises` adds no signal because the same code path is tested.

- **Parametrizing two test methods that differ only by an input literal.**
  Example: `test_with_ascii` and `test_with_unicode` share identical structure with one different string. Combining them with `pytest.mark.parametrize` saves a few lines but reduces no real maintenance burden.

- **Adding logs for events the user already sees in the UI.**
  Example: Demanding a `logger.info` line on an HTTP 409 conflict response when the response triggers a modal dialog in the user's browser that surfaces the same information. The log adds nothing operators can't observe through the UI.

- **Cosmetic regex / anchor cleanup that does not change behavior.**
  Example: Suggesting that `^` and `$` be removed from a regex used exclusively with `fullmatch()` because the anchors are redundant. Both forms are correct; the change is pure documentation noise.

- **Bundle size or performance concerns the PR description explicitly accepts.**
  Example: The PR description states "Bundle goes from 30 KB to 240 KB gzipped — acceptable for an internal tool; lazy-loading is a future optimization." Flagging the bundle increase ignores the explicit deferral.

- **Accessibility convention deviations without articulated user-impact in the actual deployment.**
  Example: Demanding an Esc-key handler on a modal dialog in an authenticated internal tool whose user audience does not include screen-reader or keyboard-only users, where both buttons are visible with `autofocus` on the safe default. Spec compliance is not the same as user impact.

## How to use this

When reviewing, before flagging an issue:
1. Try to articulate the harm in one sentence — concrete, in terms of real users or maintainers.
2. Check whether the issue matches one of the don't-flag patterns above.
3. Check whether the PR description already accepted the tradeoff.

If steps 1-3 leave the issue standing, flag it. Otherwise drop it.

---

## Generalization guidance

When a new noise pattern emerges in review:
- **First check if it can be fixed at the source.** Many noise patterns disappear if the underlying convention or format changes. A "don't flag X" rule is a band-aid; eliminating X structurally is higher leverage.
- **Add a rule only when the source can't be fixed.** And ground each rule in a specific, reproducible case.
- **Generalize across cases only after ≥3 examples.** A single instance is anecdote, not pattern.
