# GPU Strategy

GPU workloads run in Worker containers.

Requirements:

NVIDIA drivers
CUDA compatibility
PyTorch GPU support


Do not load models repeatedly.

Preferred:

Container start
-> model loading
-> inference requests
