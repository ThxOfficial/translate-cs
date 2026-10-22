# Mathematical subdomain review rules

Read the relevant items when the source uses these concepts. These are review lenses, not extra assumptions to insert into a translation. Bind the author's definition and notation in document context with exact evidence. Leave unfamiliar or conflicting senses unresolved rather than guessing.

- **Linear algebra and numerical methods:** distinguish transpose/conjugate transpose, eigenvalues/singular values, positive definite/semidefinite, norm/seminorm, exact equality/approximation, conditioning/stability. Keep matrix shapes, index ranges, and row/column conventions. Never replace a norm or simplify an expression to make prose smoother.
- **Probability and statistics:** distinguish independence/uncorrelatedness, conditional/marginal distributions, unbiasedness/consistency, probability/almost-sure convergence, confidence/credible intervals, percentage/percentage point changes. Preserve the population, sampling assumptions, and conditioning events stated by the author.
- **Optimization and learning theory:** preserve convex/strictly convex/strongly convex, local/global extrema, upper/lower bounds, worst-case/expected/high-probability guarantees. Check quantifiers, constants, asymptotic regimes, optimization direction and assumptions before translating a convergence claim.
- **Logic, algorithms and formal methods:** distinguish necessity/sufficiency, soundness/completeness, decidability/computability, existential/universal claims and constructive/nonconstructive proofs. Keep theorem/proof relationships, reduction direction, computational model and complexity qualifiers. `complete` in a proof system is not a claim that a data set has no missing rows.

Review negation and modality in every domain: “not necessarily”, “only if”, “if and only if”, “there exists”, and “under the assumption” must preserve the source's logical force. These rules do not establish the correctness of an author's theorem or empirical claim.
