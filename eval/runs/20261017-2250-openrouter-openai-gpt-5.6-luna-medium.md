# openrouter / openai/gpt-5.6-luna (thinking medium)

Run 2026-10-17T19:50:27Z, app 1642d16c5486+dirty, set 5c06a5a0748d, embedder BAAI/bge-small-en-v1.5.

- correct: 91%   grounded: 88%   right page: 85%
- valid quotes: 93% of 1.4 per answer; repairs 6
- abstained on unanswerable: 89%
- errors: 0
- judge (deepseek:deepseek-v4-pro): correct 100%, abstained 89%, quote supports claim 72%, answers with uncited extra detail 29%

| id | kind | result | facts | quotes | right page | tools |
|---|---|---|---|---|---|---|
| ff-1 | lookup | correct | 1/1 | 1/1 | yes | 3 |
| ff-2 | mechanism | correct | 2/2 | 1/1 | yes | 3 |
| ff-3 | lookup | correct | 2/2 | 4/4 | yes | 5 |
| ff-4 | unanswerable | **answered** |  | 1/1 |  | 6 |
| eqprop-1 | lookup | correct | 2/2 | 1/1 | yes | 2 |
| eqprop-2 | mechanism | correct | 1/1 | 1/1 | yes | 2 |
| eqprop-3 | mechanism | correct | 2/2 | 2/3 (repaired) | yes | 4 |
| eqprop-4 | unanswerable | abstained |  | 1/1 |  | 9 |
| fa-1 | lookup | correct | 2/2 | 1/1 | yes | 2 |
| fa-2 | mechanism | **wrong** | 1/2 | 3/3 | yes | 4 |
| fa-3 | lookup | correct | 2/2 | 1/1 | yes | 1 |
| fa-4 | unanswerable | abstained |  | 1/1 |  | 1 |
| lib-a1 | find-paper | correct | 1/1 | 1/1 | yes | 11 |
| lib-a2 | find-paper | correct | 1/1 | 2/2 | yes | 8 |
| kh-1 | lookup | correct | 2/2 | 0/1 (repaired) |  | 2 |
| kh-2 | lookup | correct | 2/2 | 2/2 | yes | 1 |
| kh-3 | mechanism | correct | 2/2 | 1/1 | yes | 1 |
| kh-4 | unanswerable | abstained |  | 2/2 |  | 10 |
| sh-1 | lookup | correct | 2/2 | 1/1 | yes | 3 |
| sh-2 | mechanism | correct | 2/2 | 2/2 | yes | 1 |
| sh-3 | lookup | correct | 2/2 | 1/1 | yes | 1 |
| sh-4 | unanswerable | abstained |  | 0/0 |  | 2 |
| hdl-1 | mechanism | correct | 2/2 | 2/2 | yes | 4 |
| hdl-2 | mechanism | correct | 2/2 | 1/1 | yes | 1 |
| hdl-3 | lookup | correct | 1/1 | 1/1 | yes | 1 |
| hdl-4 | unanswerable | abstained |  | 1/1 |  | 7 |
| lib-b1 | find-paper | correct | 1/1 | 2/2 (repaired) | yes | 8 |
| lib-b2 | find-paper | correct | 1/1 | 2/2 | yes | 4 |
| sdm-1 | lookup | correct | 2/2 | 1/1 |  | 1 |
| sdm-2 | mechanism | correct | 2/2 | 4/4 | yes | 8 |
| sdm-3 | mechanism | correct | 2/2 | 3/3 (repaired) |  | 5 |
| sdm-4 | unanswerable | abstained |  | 1/1 |  | 3 |
| illing-1 | lookup | **wrong** | 0/1 | 0/1 (repaired) |  | 5 |
| illing-2 | mechanism | correct | 1/1 | 1/2 (repaired) | yes | 4 |
| illing-3 | lookup | correct | 2/2 | 1/1 | yes | 2 |
| illing-4 | unanswerable | abstained |  | 1/1 |  | 2 |
| ewc-1 | lookup | correct | 1/1 | 1/1 | yes | 2 |
| ewc-2 | mechanism | **wrong** | 1/2 | 1/1 | yes | 2 |
| ewc-3 | mechanism | correct | 2/2 | 1/1 | yes | 2 |
| ewc-4 | unanswerable | abstained |  | 0/0 |  | 2 |
| lib-c1 | find-paper | correct | 1/1 | 1/1 | yes | 3 |
| lib-c2 | find-paper | correct | 1/1 | 1/1 |  | 9 |

## Answers to the unanswerable questions

**ff-4**: What numerical value of the goodness threshold theta (in p(positive) = sigma(sum of y_j^2 - theta)) does the paper use when training FF on MNIST?

> The paper uses **$\theta = 2$** for the goodness threshold when training FF on MNIST. It introduces the probability as
> 
> > “$p(\mathrm{positive}) = \sigma\left(\sum_j y_j^2-\theta\right)$” [p. 3](citation:1)
> 
> 

**eqprop-4**: What MNIST test error does a backpropagation-trained network with the same architecture reach in this paper's experiments, as a baseline for Equilibrium Propagation?

> The paper **does not report a separate backpropagation-trained baseline with the same architecture**. It reports only Equilibrium Propagation’s results: **about 2–3% MNIST generalization (test/validation) error, depending on architecture** [p. 9](citation:1).

**fa-4**: What test error does feedback alignment achieve on CIFAR-10 in this paper?

> This paper does **not report a CIFAR-10 test error**. The experiments use **MNIST** handwritten digits. For the main nonlinear experiment, feedback alignment achieves **2.1% mean test error**: “Feedback alignment learns just as quickly, achieving 2.1% mean error” [p. 4](citation:1).

**kh-4**: Over how many random seeds or independent runs were the reported MNIST and CIFAR-10 errors averaged, and what are their standard deviations?

> The paper **does not report averaging over multiple random seeds or independent runs**, nor does it report standard deviations for either dataset.
> 
> It presents single error figures:
> 
> - **MNIST:** biological network test error **1.52%** [p. 6](citation:1)
> - **CIFAR-10:** biological network test error **49.25%** [p. 7](citation:2)
> 
> Thus, the number of runs and corresponding standard deviations are **not specified**.

**sh-4**: What test accuracy does SoftHebb achieve on CIFAR-100?

> The paper does **not report a CIFAR-100 test accuracy**. It reports experiments on **F-MNIST and CIFAR-10**, but “CIFAR-100” does not appear in the PDF.

**hdl-4**: Which optimizer (e.g. Adam or SGD with momentum) was used to train the linear classifier head on top of the SoftHebb layers?

> The paper **does not specify the optimizer** used for the linear classifier head. It reports the batch size, number of epochs, and learning-rate schedule—initial learning rate `0.001`, halved at specified milestones—but does not say whether this was Adam, SGD with momentum, or another optimizer. [p. 18](citation:1)

**sdm-4**: What accuracy does the SDMLP reach on Split Tiny-ImageNet in the class-incremental setting?

> The paper does **not report a Split Tiny-ImageNet result**. “Tiny-ImageNet” does not appear in the PDF; the class-incremental experiments reported are primarily on **Split CIFAR-10**, where SDMLP reaches **0.71 validation accuracy** (and SDMLP+EWC reaches 0.86) [p. 6](citation:1).

**illing-4**: What test accuracy does the localized random Gabor (l-RG) network reach on Fashion-MNIST?

> The paper does **not report a Fashion-MNIST result** for the localized random Gabor (**l-RG**) network. It evaluates the models on **MNIST and CIFAR-10** only. [p. 1](citation:1)

**ewc-4**: What accuracy does EWC achieve on the Split CIFAR-100 benchmark in this paper?

> This paper does **not report an EWC accuracy on Split CIFAR-100**. “CIFAR” does not appear in the paper; its experiments use **split MNIST** and sequential **Atari 2600** games.

## Wrong answers

**fa-2** (1/2): B and W never communicate directly. How, according to the authors, does information about the fixed random feedback matrix B end up shaping the forward output weights W?

> According to the authors, **B influences W indirectly through the forward weights between the input and hidden layer, \(W'\)**—not by sending information directly to \(W\).
> 
> The sequence is:
> 
> 1. The output-weight update is ordinary error-driven learning:
>    \[
>    \Delta W \propto -e h^{T}.
>    \]
> 
> 2. The hidden-layer update uses the fixed feedback matrix:
>    \[
>    \Delta W' \propto (Be)x^{T}.
>    \]
>    Thus, \(B\) determines the modulatory signal \(d=Be\) that shapes how \(W'\) changes. [p. 3](citation:1)
> 
> 3. Those changes in \(W'\) alter the hidden activity \(h=W'x\). Since \(W\) is trained from the correlation \(e h^T\), the changing hidden activity makes the updates to \(W\) statistically favor an orientation aligned with \(B^T\).
> 
> So the information route is:
> 
> \[
> B \longrightarrow W' \longrightarrow h \longrightarrow W.
> \]
> 
> The authors summarize this explicitly: “Synaptic weight information from \(B\) flows back into \(W'\) (1) and then forward into \(W\) (2).” As \(W\) aligns with \(B^T\), \(B\) begins to function like \(W^T\), making its error signals useful for training the hidden layers. [p. 5](citation:2)
> 
> A useful way to see the causal test is their three-phase experiment: freeze \(W\) while \(W'\) learns—little improvement occurs; then freeze \(W'\) while \(W\) learns—this aligns \(W\) with \(B^T\); finally let \(W'\) learn again, and error now falls because the alignment has been established. [p. 5](citation:3)

**illing-1** (0/1): Which receptive-field (patch) size do the authors find works best for the localized random projection networks on MNIST?

> The authors find that a **$10 \times 10$-pixel patch** works best for localized random projection networks on MNIST ($\delta = 10$). They note that this optimum is more pronounced with larger hidden layers; full connectivity corresponds to $\delta = 28$. [p. 6](citation:1)

**ewc-2** (1/2): Based on their weight-perturbation test on a single-game DQN, what do the authors identify as the chief limitation of their EWC implementation?

> The authors identify **under-estimation of parameter uncertainty** as EWC’s chief limitation. The test showed that perturbations in the Fisher-estimated **nullspace**—which EWC assumes should not affect performance—damaged performance just as much as perturbations in the inverse-Fisher direction. Thus, EWC is **overconfident that some parameters are unimportant**: [p. 6](citation:1)

