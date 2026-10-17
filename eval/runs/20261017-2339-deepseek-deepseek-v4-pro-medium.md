# deepseek / deepseek-v4-pro (thinking medium)

Run 2026-10-17T20:39:31Z, app 4eba46d2bb24, set 5c06a5a0748d, embedder BAAI/bge-small-en-v1.5.

- correct: 100%   grounded: 100%   right page: 100%
- valid quotes: 99% of 2.4 per answer; repairs 5
- abstained on unanswerable: 100%
- errors: 0
- judge (deepseek:deepseek-v4-pro): correct 97%, abstained 100%, quote supports claim 80%, answers with uncited extra detail 55%

| id | kind | result | facts | quotes | right page | tools |
|---|---|---|---|---|---|---|
| ff-1 | lookup | correct | 1/1 | 1/1 | yes | 3 |
| ff-2 | mechanism | correct | 2/2 | 3/3 | yes | 4 |
| ff-3 | lookup | correct | 2/2 | 6/6 | yes | 3 |
| ff-4 | unanswerable | abstained |  | 1/1 |  | 9 |
| eqprop-1 | lookup | correct | 2/2 | 6/6 | yes | 6 |
| eqprop-2 | mechanism | correct | 1/1 | 2/2 | yes | 3 |
| eqprop-3 | mechanism | correct | 2/2 | 3/3 (repaired) | yes | 6 |
| eqprop-4 | unanswerable | abstained |  | 2/2 |  | 22 |
| fa-1 | lookup | correct | 2/2 | 2/2 | yes | 3 |
| fa-2 | mechanism | correct | 2/2 | 3/3 | yes | 5 |
| fa-3 | lookup | correct | 2/2 | 2/2 | yes | 5 |
| fa-4 | unanswerable | abstained |  | 1/1 |  | 6 |
| lib-a1 | find-paper | correct | 1/1 | 2/2 | yes | 2 |
| lib-a2 | find-paper | correct | 1/1 | 2/3 (repaired) | yes | 6 |
| kh-1 | lookup | correct | 2/2 | 1/1 | yes | 2 |
| kh-2 | lookup | correct | 2/2 | 3/3 | yes | 3 |
| kh-3 | mechanism | correct | 2/2 | 3/3 (repaired) | yes | 4 |
| kh-4 | unanswerable | abstained |  | 2/2 |  | 13 |
| sh-1 | lookup | correct | 2/2 | 1/1 | yes | 3 |
| sh-2 | mechanism | correct | 2/2 | 1/1 | yes | 3 |
| sh-3 | lookup | correct | 2/2 | 1/1 | yes | 5 |
| sh-4 | unanswerable | abstained |  | 1/1 |  | 2 |
| hdl-1 | mechanism | correct | 2/2 | 4/4 (repaired) | yes | 7 |
| hdl-2 | mechanism | correct | 2/2 | 2/2 | yes | 3 |
| hdl-3 | lookup | correct | 1/1 | 2/2 | yes | 4 |
| hdl-4 | unanswerable | abstained |  | 1/1 |  | 8 |
| lib-b1 | find-paper | correct | 1/1 | 2/2 | yes | 1 |
| lib-b2 | find-paper | correct | 1/1 | 5/5 | yes | 4 |
| sdm-1 | lookup | correct | 2/2 | 2/2 | yes | 3 |
| sdm-2 | mechanism | correct | 2/2 | 3/3 | yes | 4 |
| sdm-3 | mechanism | correct | 2/2 | 4/4 | yes | 5 |
| sdm-4 | unanswerable | abstained |  | 0/0 |  | 4 |
| illing-1 | lookup | correct | 1/1 | 1/1 | yes | 3 |
| illing-2 | mechanism | correct | 1/1 | 2/2 | yes | 3 |
| illing-3 | lookup | correct | 2/2 | 6/6 | yes | 9 |
| illing-4 | unanswerable | abstained |  | 1/1 |  | 9 |
| ewc-1 | lookup | correct | 1/1 | 3/3 | yes | 6 |
| ewc-2 | mechanism | correct | 2/2 | 1/1 | yes | 3 |
| ewc-3 | mechanism | correct | 2/2 | 2/2 | yes | 2 |
| ewc-4 | unanswerable | abstained |  | 1/1 (repaired) |  | 2 |
| lib-c1 | find-paper | correct | 1/1 | 4/4 | yes | 5 |
| lib-c2 | find-paper | correct | 1/1 | 4/4 | yes | 3 |

## Answers to the unanswerable questions

**ff-4**: What numerical value of the goodness threshold theta (in p(positive) = sigma(sum of y_j^2 - theta)) does the paper use when training FF on MNIST?

> The paper doesn't give a numerical value for $\theta$ on MNIST. It only introduces it generically in the probability formula on p. 3 as [p. 3](citation:1), and nowhere in the MNIST experiments (or anywhere else I checked) does it state a concrete number for the threshold. The training details reported are things like architecture, epochs and learning rate, not the value of $\theta$.

**eqprop-4**: What MNIST test error does a backpropagation-trained network with the same architecture reach in this paper's experiments, as a baseline for Equilibrium Propagation?

> It doesn't report one. In this paper's experiments the only MNIST numbers given are for **Equilibrium Propagation itself**, not a backprop-trained network with the same architecture.
> 
> The result is stated in Section 5: *"We achieve 0.00% training error. The generalization error lies between 2 and 3% depending on the architecture (Figure 3)."* [p. 9](citation:1)
> 
> Figure 3's caption confirms the figure shows only EP's curves: *"Training and validation error for neural networks with one hidden layer of 500 units… two hidden layers… and three hidden layers… The training error eventually decreases to 0.00% in all three cases."* [p. 10](citation:2)
> 
> So the 2–3% figure is EP's own generalization error, and the paper offers **no backprop baseline** to compare it against. If you want a backprop/BPTT baseline for these architectures, that comparison only appears in later work (e.g., the 2019 NeurIPS paper reporting ~0.90% test error for BPTT on a convolutional MNIST architecture), not in this 2017 paper.
> 
> I've scrolled you to page 9, where the result is stated in Section 5. The short answer: the paper gives **no backpropagation baseline test error** — the 2–3% figure is Equilibrium Propagation's own generalization error.

**fa-4**: What test error does feedback alignment achieve on CIFAR-10 in this paper?

> This paper doesn't report any CIFAR-10 result — **CIFAR-10 does not appear in it**. Its only image benchmark is MNIST (handwritten digits).
> 
> On that MNIST task, feedback alignment achieves a **2.1% mean test error** (backprop reaches 2.4%): [p. 4](citation:1).
> 
> If you're after CIFAR-10 numbers for feedback alignment, you'd need a later paper — this one predates any CIFAR-10 evaluation of the method.

**kh-4**: Over how many random seeds or independent runs were the reported MNIST and CIFAR-10 errors averaged, and what are their standard deviations?

> The paper doesn't report this. I searched the full text for "seed", "standard deviation", "runs", "repeat", "trial" and "std" — none of them appear anywhere in the PDF.
> 
> The error rates are given as single point values with no indication of how many runs they came from or any measure of spread:
> 
> - **MNIST** (2000 hidden units): the "biological" network has training error 0.41% and test error 1.52%, [p. 6](citation:1) matching the end-to-end SGD network's test error of ≈1.5%.
> - **CIFAR-10**: the "biological" network achieves training error 44.95% and test error 49.25%, [p. 7](citation:2) versus 44.74% test error for end-to-end SGD.
> 
> So the answer is: the paper does not say how many random seeds or independent runs the reported errors were averaged over, and gives no standard deviations. Each experiment appears to have been run once, or at least only a single number per condition is reported.

**sh-4**: What test accuracy does SoftHebb achieve on CIFAR-100?

> The paper doesn't report any CIFAR-100 results — "CIFAR-100" doesn't appear anywhere in the PDF. The closest dataset tested is **CIFAR-10**, where SoftHebb reached **50.27%** test accuracy in a preliminary run: [p. 15](citation:1). So if you need CIFAR-100 numbers, this paper won't have them.

**hdl-4**: Which optimizer (e.g. Adam or SGD with momentum) was used to train the linear classifier head on top of the SoftHebb layers?

> I'll search the paper for how the classifier was trained.
> 
> The paper **does not name the optimizer** used for the linear classifier head. Neither "Adam" nor "SGD" appears anywhere in the PDF, and "optimizer" is never mentioned.
> 
> What the paper does specify for the supervised classifier training (Appendix A.6.2) is:
> 
> - cross-entropy loss, dropout regularization, mini-batches of 64
> - initial learning rate of 0.001, halved at [20%, 35%, 50%, 60%, 70%, 80%, 90%] of total epochs [p. 18](citation:1)
> 
> So the optimization algorithm itself (Adam, plain SGD, SGD with momentum, etc.) is left unspecified in the paper.

**sdm-4**: What accuracy does the SDMLP reach on Split Tiny-ImageNet in the class-incremental setting?

> The paper does **not** report any result on Split Tiny-ImageNet — that dataset never appears in the PDF. Its continual-learning experiments cover Split CIFAR10 (the main setting), CIFAR100, Split MNIST, and Split FashionMNIST, with pretraining on ImageNet32.
> 
> So there is no SDMLP accuracy figure for Split Tiny-ImageNet in this paper to quote. If you were thinking of a different dataset result (e.g. Split CIFAR10 class-incremental), I can pull that exact number.

**illing-4**: What test accuracy does the localized random Gabor (l-RG) network reach on Fashion-MNIST?

> The paper doesn't report any Fashion-MNIST result. I searched the full text — "Fashion" and "Fashion-MNIST" never appear. The paper evaluates only on **MNIST and CIFAR10** [p. 1](citation:1).
> 
> For reference, the localized random Gabor network (ℓ-RG) results it does report are (Table 3, p. 6):
> 
> - **MNIST (rate)**: 98.9 ± 0.05 %
> - **CIFAR10 (rate)**: 55.6 ± 0.2 %
> 
> So there is no ℓ-RG Fashion-MNIST accuracy in this paper — if you need one, you'd have to look elsewhere or run it yourself.

**ewc-4**: What accuracy does EWC achieve on the Split CIFAR-100 benchmark in this paper?

> This paper doesn't report a Split CIFAR-100 result at all. I searched the PDF: **"CIFAR" never appears**, and "split" only shows up in the general sense of dividing data into task A and task B for the Bayesian derivation, not as a benchmark name.
> 
> The paper's empirical demonstrations are limited to MNIST classification tasks and Atari games: [p. 1](citation:1) The section structure confirms the same scope — supervised learning (MNIST) in §2.1 and reinforcement learning (Atari) in §2.2.
> 
> So there is no EWC accuracy on Split CIFAR-100 here. That benchmark came later, in subsequent continual-learning papers that evaluated EWC against other methods. If you'd like, I can find which of those later papers reports an EWC Split CIFAR-100 number and suggest one for you to add.

## Wrong answers

