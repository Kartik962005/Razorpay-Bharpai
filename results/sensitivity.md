# Sensitivity

500 cases, seed 42. Two questions, each answered by re-running the whole batch.

## default assumptions

Every behaviour prior in `sim/config.yaml` multiplied by each factor.

| policy | priors x0.7 | priors x1.0 | priors x1.3 |
|---|---|---|---|
| do_nothing | ₹452,799 | ₹453,197 | ₹463,291 |
| platform | ₹1,357,066 | ₹1,448,095 | ₹1,552,481 |
| naive | ₹1,491,838 | ₹1,581,044 | ₹1,685,896 |
| rules | ₹1,545,836 | ₹1,723,840 | ₹1,894,037 |

Ranking unchanged across the range: rules > naive > platform > do_nothing.

## hostile assumptions

As above, but with every assumption that punishes careless recovery turned down: night-time contact barely annoys anyone and never triggers a dispute, customers tolerate twice as many messages before complaining or opting out, retrying a risk-declined payment never causes a chargeback, and the chargeback fee is zero.

| policy | priors x0.7 | priors x1.0 | priors x1.3 |
|---|---|---|---|
| do_nothing | ₹452,799 | ₹453,197 | ₹463,291 |
| platform | ₹1,448,751 | ₹1,539,280 | ₹1,642,167 |
| naive | ₹1,771,174 | ₹1,803,196 | ₹1,872,355 |
| rules | ₹1,913,928 | ₹2,005,890 | ₹2,147,562 |

Ranking unchanged across the range: rules > naive > platform > do_nothing.
