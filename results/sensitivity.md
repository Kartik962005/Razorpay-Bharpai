# Sensitivity

500 cases, seed 42. Every behaviour prior in `sim/config.yaml` multiplied by each factor, and the whole batch re-run. Net rupees recovered:

| policy | priors x0.7 | priors x1.0 | priors x1.3 |
|---|---|---|---|
| do_nothing | ₹452,799 | ₹453,197 | ₹463,291 |
| naive | ₹1,491,838 | ₹1,581,044 | ₹1,685,896 |
| rules | ₹1,545,836 | ₹1,723,840 | ₹1,894,037 |

The ranking of the policies is unchanged across the range, so the conclusion does not rest on the priors being accurate.
