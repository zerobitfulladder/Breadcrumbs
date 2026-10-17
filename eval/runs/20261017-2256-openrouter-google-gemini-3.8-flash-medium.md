# openrouter / google/gemini-3.8-flash (thinking medium)

Run 2026-10-17T19:56:08Z, app 1642d16c5486+dirty, set 5c06a5a0748d, embedder BAAI/bge-small-en-v1.5.

- correct: 76%   grounded: 76%   right page: 96%
- valid quotes: 100% of 3.1 per answer; repairs 8
- abstained on unanswerable: 78%
- errors: 2
- judge (deepseek:deepseek-v4-pro): correct 91%, abstained 78%, quote supports claim 73%, answers with uncited extra detail 57%

| id | kind | result | facts | quotes | right page | tools |
|---|---|---|---|---|---|---|
| ff-1 | lookup | correct | 1/1 | 3/3 | yes | 2 |
| ff-2 | mechanism | **wrong** | 1/2 | 3/3 | yes | 4 |
| ff-3 | lookup | **wrong** | 0/2 | 3/3 | yes | 3 |
| ff-4 | unanswerable | **error**: Stopped after 30 rounds of tool use without reaching an answ |  | 0/0 |  | 30 |
| eqprop-1 | lookup | correct | 2/2 | 3/3 | yes | 5 |
| eqprop-2 | mechanism | correct | 1/1 | 4/4 | yes | 2 |
| eqprop-3 | mechanism | correct | 2/2 | 5/5 (repaired) | yes | 6 |
| eqprop-4 | unanswerable | abstained |  | 2/2 (repaired) |  | 13 |
| fa-1 | lookup | **wrong** | 0/2 | 3/3 | yes | 2 |
| fa-2 | mechanism | correct | 2/2 | 5/5 | yes | 3 |
| fa-3 | lookup | correct | 2/2 | 3/3 | yes | 3 |
| fa-4 | unanswerable | abstained |  | 2/2 |  | 2 |
| lib-a1 | find-paper | correct | 1/1 | 2/2 | yes | 1 |
| lib-a2 | find-paper | correct | 1/1 | 3/3 (repaired) | yes | 6 |
| kh-1 | lookup | correct | 2/2 | 1/1 | yes | 1 |
| kh-2 | lookup | correct | 2/2 | 4/4 | yes | 3 |
| kh-3 | mechanism | correct | 2/2 | 4/4 | yes | 3 |
| kh-4 | unanswerable | abstained |  | 4/4 |  | 8 |
| sh-1 | lookup | correct | 2/2 | 3/3 (repaired) | yes | 2 |
| sh-2 | mechanism | **wrong** | 1/2 | 3/3 | yes | 2 |
| sh-3 | lookup | correct | 2/2 | 1/1 | yes | 1 |
| sh-4 | unanswerable | abstained |  | 2/2 |  | 3 |
| hdl-1 | mechanism | **wrong** | 1/2 | 5/5 | yes | 10 |
| hdl-2 | mechanism | **wrong** | 1/2 | 3/3 | yes | 1 |
| hdl-3 | lookup | correct | 1/1 | 2/2 | yes | 2 |
| hdl-4 | unanswerable | **error**: Stopped after 30 rounds of tool use without reaching an answ |  | 0/0 |  | 30 |
| lib-b1 | find-paper | correct | 1/1 | 2/2 | yes | 2 |
| lib-b2 | find-paper | correct | 1/1 | 3/3 | yes | 5 |
| sdm-1 | lookup | correct | 2/2 | 5/5 (repaired) |  | 8 |
| sdm-2 | mechanism | correct | 2/2 | 6/6 | yes | 11 |
| sdm-3 | mechanism | correct | 2/2 | 10/10 (repaired) | yes | 5 |
| sdm-4 | unanswerable | abstained |  | 2/2 |  | 3 |
| illing-1 | lookup | correct | 1/1 | 2/2 | yes | 7 |
| illing-2 | mechanism | **wrong** | 0/1 | 4/4 | yes | 2 |
| illing-3 | lookup | correct | 2/2 | 5/5 (repaired) | yes | 10 |
| illing-4 | unanswerable | abstained |  | 3/3 |  | 3 |
| ewc-1 | lookup | correct | 1/1 | 1/1 | yes | 2 |
| ewc-2 | mechanism | **wrong** | 0/2 | 3/3 | yes | 2 |
| ewc-3 | mechanism | correct | 2/2 | 3/3 | yes | 2 |
| ewc-4 | unanswerable | abstained |  | 1/1 |  | 1 |
| lib-c1 | find-paper | correct | 1/1 | 4/4 | yes | 2 |
| lib-c2 | find-paper | correct | 1/1 | 5/5 (repaired) | yes | 5 |

## Answers to the unanswerable questions

**ff-4**: What numerical value of the goodness threshold theta (in p(positive) = sigma(sum of y_j^2 - theta)) does the paper use when training FF on MNIST?

> (none)

**eqprop-4**: What MNIST test error does a backpropagation-trained network with the same architecture reach in this paper's experiments, as a baseline for Equilibrium Propagation?

> **This paper does not include or report a backpropagation baseline experiment.**
> 
> The experimental section (Section 5, [pp. 9–11](citation:1)) evaluates only Equilibrium Propagation applied to continuous Hopfield networks with 1, 2, and 3 hidden layers of 500 units on MNIST:
> 
> * The authors report: [p. 9](citation:2) (plotted as validation error curves in Figure 3).
> * Backpropagation is treated purely theoretically throughout the paper (e.g., in Sections 1, 2.3, and 3.5)—as the exact gradient-computation framework that Equilibrium Propagation approximates mathematically—rather than evaluated as an empirical baseline run side-by-side in Section 5.

**fa-4**: What test error does feedback alignment achieve on CIFAR-10 in this paper?

> **This paper does not evaluate feedback alignment on CIFAR-10.** 
> 
> The only standard image classification benchmark tested in the paper is **MNIST** handwritten digit recognition:
> 
> - In a feedforward network of logistic units ($784\text{–}1000\text{–}10$), feedback alignment achieves **$2.1\%$ mean test error** (averaged over 20 runs), compared to **$2.4\%$** for backpropagation [p. 4](citation:1).
> - Even when $50\%$ of connections in the forward and feedback weight matrices are removed at random, feedback alignment still achieves **$2.4\%$ mean error** [p. 4](citation:2).
> - It is also tested on MNIST using a deeper spiking network ($784\text{–}1500\text{–}1500\text{–}1500\text{–}1000$) whose units integrate activity over time.
> 
> CIFAR-10 benchmarks for feedback alignment and related variants (such as direct feedback alignment) appeared in follow-up literature, such as [Direct Feedback Alignment Provides an Alternative to Backpropagation in Deep Neural Networks](paper:10.5555/3157096.3157202) (Nøkland, 2016).

**kh-4**: Over how many random seeds or independent runs were the reported MNIST and CIFAR-10 errors averaged, and what are their standard deviations?

> The paper **does not average over multiple runs or random seeds, and reports no standard deviations**.
> 
> Instead, all reported error rates are given as single point estimates:
> 
> * **MNIST**:
>   * "Biological" network: test error [p. 6](citation:1)
>   * End-to-end SGD: test error [p. 6](citation:2)
> * **CIFAR-10**:
>   * "Biological" network: [p. 7](citation:3)
>   * End-to-end SGD: test error [p. 7](citation:4)
> 
> The terms "seed", "standard deviation", "variance", or "independent runs" do not appear anywhere in the paper or its appendix.

**sh-4**: What test accuracy does SoftHebb achieve on CIFAR-100?

> This paper **does not evaluate or report results on CIFAR-100**. 
> 
> The experiments are restricted to simpler benchmarks (MNIST, Fashion-MNIST, and CIFAR-10) because the paper focuses on shallow single-layer WTA networks. The authors note:
> 
> * For the CIFAR family, they only report preliminary results on **CIFAR-10**, where SoftHebb reaches an accuracy of [p. 15](citation:1).
> * They explicitly list evaluating deeper architectures on more complex datasets as a limitation left for future work: [p. 26](citation:2).

**hdl-4**: Which optimizer (e.g. Adam or SGD with momentum) was used to train the linear classifier head on top of the SoftHebb layers?

> (none)

**sdm-4**: What accuracy does the SDMLP reach on Split Tiny-ImageNet in the class-incremental setting?

> The paper **does not evaluate on Split Tiny-ImageNet**. 
> 
> The authors test the SDMLP in the class-incremental setting on:
> - **Split CIFAR-10** (their main benchmark, where SDMLP reaches 0.71 validation accuracy alone and 0.77 with EWC [p. 6](citation:1))
> - **CIFAR-100** (Appendix F.2, p. 43)
> - **Split MNIST** (Appendix F.3, p. 46)
> - **Split FashionMNIST** (Appendix F.4, p. 47)
> 
> ImageNet is used only for pretraining representations (specifically, using a frozen ConvMixer pretrained on ImageNet32 and pretraining the SDMLP on ImageNet32 embeddings [p. 6](citation:2)), rather than as a split continual learning target dataset.

**illing-4**: What test accuracy does the localized random Gabor (l-RG) network reach on Fashion-MNIST?

> The paper does not test on or report results for **Fashion-MNIST**. 
> 
> The authors only evaluate their models on two datasets [p. 1](citation:1):
> - **MNIST** (digit classification)
> - **CIFAR-10** (object classification)
> 
> For comparison, on **MNIST**, the rate-based $l$-RG model reaches **98.9%** test accuracy [p. 3](citation:2), and the spiking LIF $l$-RG model reaches **98.6%** [p. 3](citation:3).

**ewc-4**: What accuracy does EWC achieve on the Split CIFAR-100 benchmark in this paper?

> This paper **does not evaluate on Split CIFAR-100** (the term *CIFAR* does not appear in the text). 
> 
> In this work, Kirkpatrick et al. evaluate Elastic Weight Consolidation (EWC) on two benchmarks [p. 1](citation:1):
> - **Random Permutations of MNIST** (supervised learning)
> - **Atari 2600 games** trained sequentially (reinforcement learning with DQN)
> 
> Split CIFAR-100 was popularized as a continual learning benchmark in later papers that built on or benchmarked against EWC (such as Zenke et al., 2017 [Continual Learning Through Synaptic Intelligence](paper:10.48550/arXiv.1703.04200)).

## Wrong answers

**ff-2** (1/2): How does FF stop the next hidden layer from trivially telling positive from negative data by using the previous layer's activity?

> In section 2.1, Hinton explains that if the raw activations of the first layer were passed directly as inputs to the second, the second layer could trivially classify positive from negative samples simply by measuring overall magnitude, without discovering any new features:
> 
> > [p. 3](citation:1)
> 
> Hinton summarizes this distinction geometrically:
> * **Length:** Represents the overall energy or goodness ($\sum_j y_j^2$) used by that layer's own local objective function.
> * **Orientation:** [p. 3](citation:2)
> 
> By dividing by the vector's length—specifically using [p. 3](citation:3)—the scalar goodness information is completely stripped out, forcing each subsequent layer to find structure solely in the patterns of relative neuron activations.

**ff-3** (0/2): What does the paper report about separating FF's positive and negative phases in time, i.e. doing many updates on positive data and then many on negative data (as in sleep)?

> In **Section 5 ("Sleep")**, Hinton notes that while separating the phases would make FF much more biologically plausible (positive passes during wake, negative passes during sleep), doing so does not work cleanly under the standard setup:
> 
> - **Retraction of earlier positive results**: Hinton explains that in an early draft, he reported success alternating blocks of positive and negative updates on a next-character prediction task, but notes: [p. 9](citation:1)
> - **Severe optimization constraints**: When using the standard goodness function, [p. 9](citation:2)
> - **Open problem**: Hinton considers this an unresolved question rather than a solved feature, writing: [p. 9](citation:3)

**fa-1** (0/2): On MNIST with the 784-1000-10 network of logistic units, what mean test errors does the paper report for backprop and for feedback alignment?

> On the MNIST handwritten digit recognition task using a 784-1000-10 network of logistic units, the authors report:
> 
> * **Backprop:** [p. 4](citation:1)
> * **Feedback alignment:** [p. 4](citation:2) (also averaged over 20 runs)
> 
> They also note that even when randomly zeroing out 50% of the forward and feedback matrices (giving only a 25% chance of reciprocal connections), feedback alignment still achieved [p. 4](citation:3).

**sh-2** (1/2): How do the authors measure the cross-entropy loss of the unsupervised SoftHebb network during training, given that the true hidden causes are not available?

> Because the true hidden causes $C$ are unknown, the authors introduce a three-step procedure called the **post-hoc cross-entropy method** [p. 10](citation:1):
> 
> 1. **Theoretical link between causes and labels**: In causal datasets like MNIST, the true direct cause $C$ combines class labels $L$ and latent factors (e.g., handwriting style). The authors show theoretically that minimizing cross-entropy with respect to the implicit model over labels $Q(L)$ is equivalent to minimizing cross-entropy with respect to the hidden causes $Q(C)$, meaning that $\arg\min_w H_Q^C = \arg\min_w H_Q^L$.
> 2. **Missing causal structure**: Although $P(L_l \mid C)$ is unknown, it [p. 10](citation:2).
> 3. **The post-hoc evaluation protocol**:
>    - **Step (a)**: Train the SoftHebb network in an unsupervised manner until convergence.
>    - **Step (b)**: Train a supervised linear classifier on top of the converged SoftHebb representations using the dataset's ground-truth labels $L_l$.
>    - **Step (c)**: Re-run SoftHebb from the start using the identical initial weights and input sequence [p. 10](citation:3).
> 
> This allows the authors to plot the cross-entropy trajectory during unsupervised learning without giving SoftHebb access to labels or ground-truth causes during its training.

**hdl-1** (1/2): How do the authors define the new 'soft' anti-Hebbian plasticity they introduce for soft winner-take-all networks?

> The authors define their soft anti-Hebbian plasticity by directly modifying the standard SoftHebb update rule across the competing neurons in a layer [p. 5](citation:1).
> 
> ### Mathematical Definition
> 
> 1. **Standard SoftHebb Plasticity**:
>    For a layer of $K$ neurons under soft winner-take-all competition (where outputs $y_k$ are determined via softmax over weighted inputs $u_k$), the baseline update rule for weight $w_{ik}$ connecting presynaptic neuron $i$ (with activation $x_i$) to postsynaptic neuron $k$ is [p. 4](citation:2):
>    $$\Delta w_{ik}^{(\text{SoftHebb})} = \eta \, y_k (x_i - u_k w_{ik})$$
> 
> 2. **Soft Anti-Hebbian Rule**:
>    Denoting $k^* = \arg\max_k u_k$ as the maximally activated neuron, the weight update becomes:
>    $$\Delta w_{ik} = \begin{cases} 
>    +\Delta w_{ik}^{(\text{SoftHebb})} = \eta \, y_{k^*} (x_i - u_{k^*} w_{ik^*}), & \text{if } k = k^* \\ 
>    -\Delta w_{ik}^{(\text{SoftHebb})} = -\eta \, y_k (x_i - u_k w_{ik}), & \text{if } k \neq k^* 
>    \end{cases}$$
> 
> ### Key Differences from Prior Anti-Hebbian Approaches
> - **Contrast with hard-WTA models**: Earlier competitive networks applied anti-Hebbian plasticity selectively to a single $k$-th runner-up / most active neuron and required tuning additional hyperparameters [p. 5](citation:3) [p. 7](citation:4).
> - **Distributed ("soft") nature**: In multilayer SoftHebb, anti-Hebbian updates naturally scale with each non-winning neuron's softmax activation $y_k$, retaining distributed learning without adding extra hyperparameters [p. 5](citation:5).

**hdl-2** (1/2): How did the authors decide how many hidden layers to stack for each dataset?

> The authors determined the depth directly from the **spatial resolution of the input images** and the pooling operations, rather than tuning depth as an unconstrained hyperparameter.
> 
> Specifically:
> - Each layer applies a pooling operation with stride 2, which [p. 7](citation:1).
> - They set a stopping criterion: [p. 7](citation:2).
> 
> Because the stopping point depends on the starting dimensions [p. 7](citation:3), this yielded:
> - **3 hidden layers** for MNIST ($28 \times 28$) and CIFAR-10 ($32 \times 32$)
> - **4 hidden layers** for STL-10 ($96 \times 96$)
> - **5 hidden layers** for ImageNet (evaluated at an input resolution of $160 \times 160$)

**illing-2** (0/1): In the spiking LIF network, why do the authors switch off learning for a period after each change from one input pattern to the next?

> The authors disable learning during the transient period following a switch between input patterns for two related reasons:
> 
> 1. **Mixed information across patterns:** Immediately after an input switch, membrane potentials and spike traces have not fully decayed or settled into steady-state firing. Consequently, [p. 7](citation:1)
> 2. **Deterioration of learning progress:** Learning is paused for a duration of $T_{\text{trans}} = 4\tau_m$ [p. 11](citation:2) In practice, [p. 7](citation:3)
> 
> Learning is resumed only once [p. 6–7](citation:4) and the network activity cleanly reflects the current input pattern.

**ewc-2** (0/2): Based on their weight-perturbation test on a single-game DQN, what do the authors identify as the chief limitation of their EWC implementation?

> In their weight-perturbation experiments on a single-game DQN, the authors tested parameter sensitivity under three noise directions: uniform perturbations, perturbations scaled by the inverse diagonal of the Fisher Information, and perturbations restricted to the **nullspace** of the Fisher.
> 
> From this test, they observe:
> * Under their quadratic approximation, [p. 6](citation:1).
> * Empirically, however, perturbations in the nullspace affected performance just as much as perturbations in the inverse Fisher space.
> 
> Because of this discrepancy, they conclude that the network is [p. 6](citation:2), identifying the chief limitation of their current implementation as:
> 
> > [p. 6](citation:3)

