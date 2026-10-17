# deepseek / deepseek-v4-pro (thinking medium)

Run 2026-10-17T20:07:48Z, app 1642d16c5486+dirty, set 5c06a5a0748d, embedder BAAI/bge-small-en-v1.5.

- correct: 100%   grounded: 100%   right page: 96%
- valid quotes: 99% of 2.1 per answer; repairs 1
- abstained on unanswerable: 100%
- errors: 0
- judge (deepseek:deepseek-v4-pro): correct 97%, abstained 100%, quote supports claim 83%, answers with uncited extra detail 45%

| id | kind | result | facts | quotes | right page | tools |
|---|---|---|---|---|---|---|
| ff-1 | lookup | correct | 1/1 | 2/2 | yes | 3 |
| ff-2 | mechanism | correct | 2/2 | 2/2 | yes | 4 |
| ff-3 | lookup | correct | 2/2 | 5/5 | yes | 1 |
| ff-4 | unanswerable | abstained |  | 3/3 |  | 13 |
| eqprop-1 | lookup | correct | 2/2 | 3/3 | yes | 5 |
| eqprop-2 | mechanism | correct | 1/1 | 1/1 | yes | 3 |
| eqprop-3 | mechanism | correct | 2/2 | 3/3 | yes | 3 |
| eqprop-4 | unanswerable | abstained |  | 1/1 |  | 12 |
| fa-1 | lookup | correct | 2/2 | 2/2 | yes | 3 |
| fa-2 | mechanism | correct | 2/2 | 4/4 | yes | 2 |
| fa-3 | lookup | correct | 2/2 | 3/3 | yes | 2 |
| fa-4 | unanswerable | abstained |  | 1/1 |  | 5 |
| lib-a1 | find-paper | correct | 1/1 | 1/1 | yes | 2 |
| lib-a2 | find-paper | correct | 1/1 | 2/2 | yes | 5 |
| kh-1 | lookup | correct | 2/2 | 1/1 | yes | 4 |
| kh-2 | lookup | correct | 2/2 | 3/3 | yes | 3 |
| kh-3 | mechanism | correct | 2/2 | 3/3 | yes | 1 |
| kh-4 | unanswerable | abstained |  | 5/5 |  | 13 |
| sh-1 | lookup | correct | 2/2 | 1/1 | yes | 3 |
| sh-2 | mechanism | correct | 2/2 | 2/2 | yes | 4 |
| sh-3 | lookup | correct | 2/2 | 1/1 | yes | 2 |
| sh-4 | unanswerable | abstained |  | 2/2 |  | 2 |
| hdl-1 | mechanism | correct | 2/2 | 3/3 | yes | 6 |
| hdl-2 | mechanism | correct | 2/2 | 2/2 | yes | 2 |
| hdl-3 | lookup | correct | 1/1 | 1/1 | yes | 5 |
| hdl-4 | unanswerable | abstained |  | 1/1 |  | 7 |
| lib-b1 | find-paper | correct | 1/1 | 2/2 | yes | 2 |
| lib-b2 | find-paper | correct | 1/1 | 3/3 | yes | 2 |
| sdm-1 | lookup | correct | 2/2 | 1/1 |  | 6 |
| sdm-2 | mechanism | correct | 2/2 | 3/4 (repaired) | yes | 6 |
| sdm-3 | mechanism | correct | 2/2 | 5/5 | yes | 5 |
| sdm-4 | unanswerable | abstained |  | 1/1 |  | 5 |
| illing-1 | lookup | correct | 1/1 | 1/1 | yes | 3 |
| illing-2 | mechanism | correct | 1/1 | 1/1 | yes | 6 |
| illing-3 | lookup | correct | 2/2 | 1/1 | yes | 4 |
| illing-4 | unanswerable | abstained |  | 1/1 |  | 4 |
| ewc-1 | lookup | correct | 1/1 | 2/2 | yes | 6 |
| ewc-2 | mechanism | correct | 2/2 | 1/1 | yes | 4 |
| ewc-3 | mechanism | correct | 2/2 | 1/1 | yes | 2 |
| ewc-4 | unanswerable | abstained |  | 1/1 |  | 2 |
| lib-c1 | find-paper | correct | 1/1 | 4/4 | yes | 4 |
| lib-c2 | find-paper | correct | 1/1 | 2/2 |  | 2 |

## Answers to the unanswerable questions

**ff-4**: What numerical value of the goodness threshold theta (in p(positive) = sigma(sum of y_j^2 - theta)) does the paper use when training FF on MNIST?

> The paper **does not give a numerical value for θ**. It introduces the threshold only symbolically in Eq. (1), where the probability that an input is positive is described as applying the logistic function to "the goodness, minus some threshold, θ" [p. 3](citation:1), and elsewhere says only that the aim is to make the goodness "well above some threshold value for real data and well below that value for negative data" [p. 2](citation:2).
> 
> No experiment section (MNIST, CIFAR-10, or otherwise) states the threshold actually used. The only "0.5" threshold in the MNIST experiments refers to something else — thresholding the blurred mask image to create the hybrid negative examples [p. 4](citation:3).
> 
> So if you're looking for a specific θ to reproduce the MNIST runs, the paper leaves it unspecified; it would have to be inferred from the code.

**eqprop-4**: What MNIST test error does a backpropagation-trained network with the same architecture reach in this paper's experiments, as a baseline for Equilibrium Propagation?

> The paper doesn't report a backpropagation baseline for the same architecture. The only MNIST numbers given are for Equilibrium Propagation itself: **0.00% training error**, with generalization error **between 2 and 3% depending on the architecture** [p. 9](citation:1). The results shown in Figure 3 and Table 2 are all for Equilibrium Propagation, not a backprop comparison. So if you're looking for the backprop-trained test error as a baseline, it isn't in this paper — the comparison you'd want would have to come from elsewhere.

**fa-4**: What test error does feedback alignment achieve on CIFAR-10 in this paper?

> This paper doesn't report a CIFAR-10 result — "CIFAR" never appears in the text. Its image benchmark is **MNIST** (handwritten digit recognition), where feedback alignment reaches **2.1% mean test error** on a 784-1000-10 network, slightly better than backprop's 2.4% [p. 4](citation:1). So there is no CIFAR-10 test error to quote from this paper.

**kh-4**: Over how many random seeds or independent runs were the reported MNIST and CIFAR-10 errors averaged, and what are their standard deviations?

> The paper doesn't report averaging over seeds or runs at all. Searching the PDF, the words **"seed", "standard deviation", and "runs" never appear**, and the results are given as single numbers with no error bars or variance.
> 
> The reported figures are single-run values:
> 
> **MNIST** (784→2000→10)
> - "biological" network: training error 0.41%, test error 1.52% [p. 6](citation:1) — stated to match the end-to-end SGD test error [p. 6](citation:2)
> - SGD baseline: training error 0%, test error ≈1.5% [p. 6](citation:3)
> 
> **CIFAR-10** (3072→2000→10)
> - "biological" network: training error 44.95%, test error 49.25% [p. 7](citation:4)
> - SGD baseline: test error 44.74% [p. 7](citation:5)
> 
> So the answer to your question: **the paper does not say** — no number of seeds/runs and no standard deviations are given for either dataset. If you want, I can note this gap on the paper.

**sh-4**: What test accuracy does SoftHebb achieve on CIFAR-100?

> SoftHebb's paper does **not** report a CIFAR-100 accuracy — "CIFAR-100" doesn't appear anywhere in the PDF. The only CIFAR dataset tested is CIFAR-10, as a preliminary extensibility experiment (Section 3.7), where SoftHebb reached **50.27%** accuracy versus 49.78% for hard WTA [p. 14](citation:1).
> 
> The authors note these were preliminary and that hyper-parameters were not tuned extensively [p. 14](citation:2).

**hdl-4**: Which optimizer (e.g. Adam or SGD with momentum) was used to train the linear classifier head on top of the SoftHebb layers?

> The paper **doesn't name an optimizer** for the linear classifier head. It only specifies the training schedule: a mini-batch of 64, trained for 50 epochs (MNIST, CIFAR-10), 100 (STL-10) or 200 (ImageNet), with a learning rate starting at 0.001 that is halved repeatedly [p. 18](citation:1).
> 
> I searched the PDF for "Adam", "SGD", "momentum" and "optimizer" — none of them appear in the methods. So the exact optimizer (e.g. Adam vs SGD with momentum) is not stated; you'd have to check the released code at `github.com/NeuromorphicComputing/SoftHebb` to find out.

**sdm-4**: What accuracy does the SDMLP reach on Split Tiny-ImageNet in the class-incremental setting?

> This paper doesn't report a Split Tiny-ImageNet result. Its continual-learning experiments are on **Split CIFAR10** (main text), plus **CIFAR100, Split MNIST and Split FashionMNIST** in the appendix — the class-incremental setting is described for Split CIFAR10 [p. 6](citation:1). A search of the PDF finds no mention of "Tiny-ImageNet" or "Tiny", so there's no SDMLP accuracy figure for that dataset here.

**illing-4**: What test accuracy does the localized random Gabor (l-RG) network reach on Fashion-MNIST?

> This paper doesn't report any Fashion-MNIST results. It evaluates only **MNIST and CIFAR10** — the scope is stated right in the abstract: "we investigate how far we can go on digit (MNIST) and object (CIFAR10) classiﬁcation" [p. 1](citation:1).
> 
> Also note the naming: the paper calls the localized Gabor network a **random Gabor (RG) filter** network, not "l-RG", and a search for both "Fashion-MNIST" and "l-RG" in the PDF returns nothing. So there's no l-RG Fashion-MNIST accuracy here to quote — the nearest numbers in the paper are its MNIST and CIFAR10 accuracies.

**ewc-4**: What accuracy does EWC achieve on the Split CIFAR-100 benchmark in this paper?

> This paper **does not evaluate EWC on Split CIFAR-100** — the benchmark doesn't appear anywhere in the PDF (`CIFAR` returns zero hits). Its experiments are the ones named in the abstract: MNIST classification tasks and Atari games, done sequentially [p. 1](citation:1).
> 
> So there is no Split CIFAR-100 accuracy to report from this paper. If you're after EWC-style numbers on Split CIFAR-100, they come from later continual-learning papers, not the original EWC paper.

## Wrong answers

