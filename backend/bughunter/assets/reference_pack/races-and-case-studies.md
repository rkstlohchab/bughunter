# Race conditions, workflow states, and published cases

Authored synthesis and explicitly attributed historical examples; reviewed 2026-11-11. No external target was tested for this pack.

## Published case: hidden state transitions

James Kettle's 2023 research reports real GitLab invitation and email-verification race conditions. One invitation example produced different behavior under concurrent requests than under a sequential baseline; display and underlying invitation state could disagree. The paper also discusses an online shop workflow where changing basket contents between payment and confirmation broke price consistency. These are the author's reported findings, not findings reproduced here. [PortSwigger: Smashing the state machine](https://portswigger.net/research/smashing-the-state-machine).

## Method inferred from that work

Map the shared object, which endpoints read/write it, and the intended invariant. Establish sequential behavior before examining concurrency. Inspect durable final state, since response counts alone can mislead. Reproduce with the fewest operations needed to expose the transition, then restore the fixture before repeating. Record timing and failure frequency rather than claiming deterministic behavior from one observation.

## Published case: recovery destination validation

GitLab's January 11, 2024 advisory for CVE-2023-7028 describes password-reset emails reaching an unverified email address. It identifies affected releases and fixed versions 16.7.2, 16.6.4, and 16.5.6 among the published updates. Use the advisory for exact version applicability; do not infer current vulnerability from the product name. [GitLab advisory](https://docs.gitlab.com/releases/patches/patch-release-gitlab-16-7-2-released/).

The testing lesson is to bind recovery delivery to verified account state and validate destination handling at the operation boundary. A food service's receipt, refund, or loyalty workflow could merit an analogous state review, but that is a hypothesis, not evidence of a defect in any service.

## Fixture limitation

The bundled food test repeats coupons within one request. It does not test thread scheduling, database isolation, duplicate callbacks, or concurrent redemption. A separate deterministic concurrency fixture would be needed to verify those properties.
