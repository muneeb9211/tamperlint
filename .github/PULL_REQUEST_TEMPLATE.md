## What this changes

## Checklist

- [ ] Tests cover the forged case **and** the benign corpus stays `INTACT`
- [ ] `ruff check`, `ruff format --check` and `mypy` pass
- [ ] New or changed rules: `docs/rules.md` regenerated with `tamperlint rules --markdown`
- [ ] Evaluation before/after (`python evals/run_eval.py --seeds 5`) pasted below, if detection changed
- [ ] No real personal documents in the diff, fixtures or screenshots
