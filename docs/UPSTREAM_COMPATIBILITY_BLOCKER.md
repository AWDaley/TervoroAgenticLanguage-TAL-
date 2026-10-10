# PlanCraft Native-Scoring Compatibility Blocker

The approved control baseline is `ybkim95/agent-scaling` tag `v2.1.3`, resolved
to commit `358d67e24c616073127ce1a8b78d4b632162848c`. Its lockfile pins
`plancraft==0.4.9`. On the current Windows/Python 3.12 environment, importing that
exact package fails before an environment can be created:

```text
KeyError: 'acacia_logs'
```

The error occurs while `plancraft.environment.recipes` builds its recipe registry.
Consequently the study cannot claim PlanCraft *native* scoring with the presently
pinned runtime. The TAL research runner therefore accepts an explicit native scorer
adapter but does not bundle a replacement runtime.

This is a methodological choice requiring approval, not an implementation detail.
Choose one of the following before sealing the main-study freeze:

1. Pin an upstream PlanCraft repair (with commit, wheel hash, and a new preflight
   evidence record).
2. Pin a reviewed minimal patch/fork at a commit and include the patch hash in the
   freeze.
3. Run the original benchmark container/environment known to load the native scorer,
   record its image digest, and use the existing scorer injection boundary.

Do not substitute target-string matching or a new evaluator for the native scorer.
