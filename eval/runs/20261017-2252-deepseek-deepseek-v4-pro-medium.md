# deepseek / deepseek-v4-pro (thinking medium)

Run 2026-10-17T19:52:57Z, app 1642d16c5486+dirty, set 5c06a5a0748d, embedder BAAI/bge-small-en-v1.5.

- correct: 97%   grounded: 97%   right page: 96%
- valid quotes: 95% of 2.6 per answer; repairs 6
- abstained on unanswerable: 100%
- errors: 0
- judge (deepseek:deepseek-v4-pro): correct 100%, abstained 100%, quote supports claim 78%, answers with uncited extra detail 54%

| id | kind | result | facts | quotes | right page | tools |
|---|---|---|---|---|---|---|
| ff-1 | lookup | correct | 1/1 | 1/1 | yes | 6 |
| ff-2 | mechanism | correct | 2/2 | 7/7 | yes | 4 |
| ff-3 | lookup | **wrong** | 0/2 | 3/3 | yes | 2 |
| ff-4 | unanswerable | abstained |  | 3/3 |  | 17 |
| eqprop-1 | lookup | correct | 2/2 | 2/2 | yes | 7 |
| eqprop-2 | mechanism | correct | 1/1 | 1/1 | yes | 3 |
| eqprop-3 | mechanism | correct | 2/2 | 3/3 (repaired) | yes | 4 |
| eqprop-4 | unanswerable | abstained |  | 1/1 |  | 16 |
| fa-1 | lookup | correct | 2/2 | 1/1 | yes | 3 |
| fa-2 | mechanism | correct | 2/2 | 4/4 | yes | 4 |
| fa-3 | lookup | correct | 2/2 | 2/2 | yes | 5 |
| fa-4 | unanswerable | abstained |  | 2/2 |  | 2 |
| lib-a1 | find-paper | correct | 1/1 | 3/3 | yes | 3 |
| lib-a2 | find-paper | correct | 1/1 | 2/2 | yes | 6 |
| kh-1 | lookup | correct | 2/2 | 1/1 | yes | 3 |
| kh-2 | lookup | correct | 2/2 | 4/4 | yes | 2 |
| kh-3 | mechanism | correct | 2/2 | 3/3 | yes | 1 |
| kh-4 | unanswerable | abstained |  | 3/3 |  | 11 |
| sh-1 | lookup | correct | 2/2 | 2/2 | yes | 3 |
| sh-2 | mechanism | correct | 2/2 | 4/4 | yes | 3 |
| sh-3 | lookup | correct | 2/2 | 1/1 | yes | 3 |
| sh-4 | unanswerable | abstained |  | 1/1 |  | 2 |
| hdl-1 | mechanism | correct | 2/2 | 5/5 | yes | 4 |
| hdl-2 | mechanism | correct | 2/2 | 2/2 |  | 2 |
| hdl-3 | lookup | correct | 1/1 | 1/1 | yes | 5 |
| hdl-4 | unanswerable | abstained |  | 2/2 |  | 8 |
| lib-b1 | find-paper | correct | 1/1 | 2/2 | yes | 4 |
| lib-b2 | find-paper | correct | 1/1 | 2/4 (repaired) | yes | 11 |
| sdm-1 | lookup | correct | 2/2 | 3/5 (repaired) | yes | 4 |
| sdm-2 | mechanism | correct | 2/2 | 10/11 (repaired) | yes | 8 |
| sdm-3 | mechanism | correct | 2/2 | 4/4 | yes | 5 |
| sdm-4 | unanswerable | abstained |  | 2/2 |  | 3 |
| illing-1 | lookup | correct | 1/1 | 1/1 | yes | 3 |
| illing-2 | mechanism | correct | 1/1 | 2/2 | yes | 5 |
| illing-3 | lookup | correct | 2/2 | 2/2 (repaired) | yes | 12 |
| illing-4 | unanswerable | abstained |  | 2/2 |  | 4 |
| ewc-1 | lookup | correct | 1/1 | 2/2 | yes | 4 |
| ewc-2 | mechanism | correct | 2/2 | 1/1 | yes | 3 |
| ewc-3 | mechanism | correct | 2/2 | 3/3 | yes | 2 |
| ewc-4 | unanswerable | abstained |  | 1/1 (repaired) |  | 1 |
| lib-c1 | find-paper | correct | 1/1 | 2/2 | yes | 3 |
| lib-c2 | find-paper | correct | 1/1 | 3/3 | yes | 2 |

## Answers to the unanswerable questions

**ff-4**: What numerical value of the goodness threshold theta (in p(positive) = sigma(sum of y_j^2 - theta)) does the paper use when training FF on MNIST?

> The paper doesn't give a numerical value for $\theta$. It introduces the threshold only symbolically, as "some threshold":
> 
> > …applying the logistic function, σ to the goodness, minus some threshold, θ $$p(\text{positive}) = \sigma\left(\sum_j y_j^2 - \theta\right)\quad(1)$$ where $y_j$ is the activity of hidden unit $j$ before layer normalization. [p. 3](citation:1)
> 
> Earlier it describes the goal the same way, without a value:
> 
> > The aim of the learning is to make the goodness be well above some threshold value for real data and well below that value for negative data. [p. 2](citation:2)
> 
> I searched the PDF for "θ =", "the threshold was set", and the word "threshold" — the only numerical threshold mentioned is the unrelated 0.5 used to binarize the mask images for negative data [p. 4](citation:3). So the MNIST experiments in the paper don't report the specific $\theta$ used; it's left as an unspecified hyperparameter.

**eqprop-4**: What MNIST test error does a backpropagation-trained network with the same architecture reach in this paper's experiments, as a baseline for Equilibrium Propagation?

> The paper doesn't report a backpropagation-trained baseline. The only MNIST figures it gives are for **Equilibrium Propagation itself**, not for a backprop network with the same architecture:
> 
> > "We achieve 0.00% training error. The generalization error lies between 2 and 3% depending on the architecture (Figure 3)." [p. 9](citation:1)
> 
> Figure 3 is captioned as showing "Training and validation error" for the 784–500–10, 784–500–500–10 and 784–500–500–500–10 networks, again only for Equilibrium Propagation — there is no backprop-trained comparison network in the experiments, so no such baseline test error is stated.

**fa-4**: What test error does feedback alignment achieve on CIFAR-10 in this paper?

> This paper does **not** report any CIFAR-10 results — the word "CIFAR" doesn't appear anywhere in the PDF. Its only image-classification benchmark is **MNIST** (handwritten digits):
> 
> - Nonlinear 784–1000–10 network: backprop reaches 2.4% mean test error, feedback alignment 2.1% [p. 4](citation:1).
> - Deep spiking network (784–1500–1500–1500–1000): 1.8% final test error with feedback alignment [p. 5](citation:2).
> 
> So if you're after feedback-alignment numbers on CIFAR-10, they aren't here — you'd need a later paper that extends the method to convolutional networks.

**kh-4**: Over how many random seeds or independent runs were the reported MNIST and CIFAR-10 errors averaged, and what are their standard deviations?

> The paper does **not** report errors averaged over random seeds or independent runs, and it gives **no standard deviations**. I searched the PDF for "seed", "standard deviation", "runs", and "average" — none of those appear in connection with the results. The only "averaging" mentioned is over examples within a minibatch for the weight update: "the ranking is done for the entire minibatch and the weight updates that result from the learning rule (2) are averaged over all examples in the minibatch" [p. 5](citation:1).
> 
> The reported errors are single numbers:
> 
> | Dataset | Training error | Test error |
> |---|---|---|
> | MNIST (biological, $n=4.5$) | 0.41% | 1.52% |
> | MNIST (SGD end-to-end) | 0% | ≈1.5% |
> | CIFAR-10 (biological) | 44.95% | 49.25% |
> | CIFAR-10 (SGD end-to-end, 100 epochs) | — | 44.74% |
> 
> MNIST: "Training error of the 'biological' network is 0.41%" and "the test error of the 'biological' algorithm, which is 1.52%" [p. 6](citation:2). CIFAR-10: "The 'biological' network achieves training error 44.95% and test error 49.25%" [p. 7](citation:3).
> 
> So each figure is a single reported run, not an average over seeds — the authors do not state how many runs were made or any measure of variance.

**sh-4**: What test accuracy does SoftHebb achieve on CIFAR-100?

> SoftHebb's paper does **not** report any result on CIFAR-100 — the dataset isn't mentioned in the PDF at all. I searched for "CIFAR-100" and found nothing.
> 
> The closest is a preliminary test on **CIFAR-10** in Section 3.7: "On CIFAR-10's preliminary results, the hard WTA and SoftHebb achieved an accuracy of 49.78% and 50.27% respectively" [p. 15](citation:1). So for CIFAR-100, there is no reported test accuracy.

**hdl-4**: Which optimizer (e.g. Adam or SGD with momentum) was used to train the linear classifier head on top of the SoftHebb layers?

> The paper doesn't name the optimizer. Neither "optimizer", "Adam", nor "SGD" appears anywhere in the PDF.
> 
> What it does specify for the classifier head is only the loss, schedule, batch size and regularization — the optimizer algorithm itself is left unstated:
> 
> > "The linear classifier on top uses a mini-batch of 64 ... For all datasets, the learning-rate has an initial value of 0.001 and is halved repeatedly at [20%, 35%, 50%, 60%, 70%, 80%, 90%] of the total number of epochs." [p. 18](citation:1)
> 
> It also says the head was "trained with cross-entropy loss, using dropout regularization" [p. 6](citation:2), again without naming an optimizer.
> 
> So, short answer: **it isn't specified** — the code would be the only place to check (https://github.com/NeuromorphicComputing/SoftHebb).

**sdm-4**: What accuracy does the SDMLP reach on Split Tiny-ImageNet in the class-incremental setting?

> The paper does **not** report any results on Split Tiny-ImageNet. Searching the PDF turns up no occurrence of "Tiny-ImageNet" at all.
> 
> The class-incremental experiments in the paper use **Split CIFAR10** as the main setting, with additional experiments on **CIFAR100, MNIST, and FashionMNIST**:
> 
> > "We use Split CIFAR10 in the class incremental setting with pretraining on ImageNet" [p. 6](citation:1)
> 
> > "We also run experiments for CIFAR100 (App. F.2), MNIST (App. F.3) and FashionMNIST (App. F.4)." [p. 6](citation:2)
> 
> So if you're after the SDMLP's Split Tiny-ImageNet accuracy, that number isn't in this paper.

**illing-4**: What test accuracy does the localized random Gabor (l-RG) network reach on Fashion-MNIST?

> The paper doesn't report any Fashion-MNIST results at all. It evaluates only two datasets — MNIST and CIFAR10 — as stated in the abstract: *"we investigate how far we can go on digit (MNIST) and object (CIFAR10) classification with biologically plausible, local learning rules"* [p. 1](citation:1).
> 
> A search of the full text finds no occurrence of "Fashion," and the localized random Gabor model is abbreviated **RG** (Random Gabor filters), not "l-RG." So there is no Fashion-MNIST accuracy figure for it in this paper — only MNIST, where the spiking RG networks achieve *"> 98.2% test accuracy on MNIST"* [p. 1](citation:2).

**ewc-4**: What accuracy does EWC achieve on the Split CIFAR-100 benchmark in this paper?

> This paper doesn't report any Split CIFAR-100 result — "CIFAR" never appears in the PDF. The supervised-learning experiments in EWC are on **MNIST**, not CIFAR, as the abstract states: [p. 1](citation:1).
> 
> Split CIFAR-100 is a benchmark that appears in later continual-learning papers (e.g. using a ResNet on CIFAR-100 split into tasks), but it isn't part of this 2016 paper's evaluation.

## Wrong answers

**ff-3** (0/2): What does the paper report about separating FF's positive and negative phases in time, i.e. doing many updates on positive data and then many on negative data (as in sleep)?

> Hinton's report in the Sleep section (§5, p. 9) is essentially **negative — he could not get the phases separated, and he retracts his earlier claim**. Specifically:
> 
> - In an early draft he reported that it *was* possible to do multiple positive updates followed by multiple negative updates "generated by the net itself, with very little loss in performance", using next-character prediction from a ten-character window. He now says: [p. 9](citation:1)
> - With the sum-of-squared-activities goodness, separating the phases works only under extreme conditions: [p. 9](citation:2)
> - He leaves open that a different goodness function might allow separation, but calls it unresolved: [p. 9](citation:3)
> 
> He also notes, by analogy, that Boltzmann machine learning "fails if many positive updates of the weights are followed by many negative updates as would be required if the negative phase corresponded to REM sleep", and speculates that if the phases *could* be separated, turning off negative-phase updates might mimic the effects of severe sleep deprivation.

