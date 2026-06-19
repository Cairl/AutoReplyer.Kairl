# AutoReplyer.Kairl — 项目长期笔记

## 运行环境
- Python 3.12 (C:\Users\Administrator\AppData\Local\Programs\Python\Python312)
- NVIDIA RTX 5070 (Blackwell, sm_120)，需要 CUDA 12.8+
- 已装 cupy-cuda12x 14.1.1 + cupy-cuda12x[ctk]（CUDA 12.9.2 wheels）
- nvidia wheels 装在 site-packages/nvidia/ 下（cuda_runtime/, cuda_nvrtc/, cublas/ 等，分散布局无统一根）
- cupy 实际通过 cuda-pathfinder 找 wheel 里的库，能正常 JIT 编译 kernel 在 sm_120 上运行
- cupy._environment 的 "CUDA path could not be detected" 警告是 wheel 模式假阳性，gpu.py 已 filterwarnings 过滤

## GPU 加速设计约定（core/gpu.py）
- cupy 可用性检测必须用"实际计算 probe"（astype+abs+std），不能只靠 cp.cuda.is_available()
  - is_available() 只检查硬件/驱动，不检查 CUDA toolkit headers
  - 只靠 is_available() 会导致 xp=cupy 但运行时崩溃，monitor 每次扫描失败、永不回复
- GPU 加速是可选增强：cupy 没装或运行时不可用时，静默回退 numpy，不崩溃程序
- gpu.py 导出：xp（cupy 或 numpy）、gpu_available、cupy_available、gpu_info、gpu_fallback_reason
- monitor.py 用 `from core.gpu import xp`，颜色匹配用 xp，控制流用 numpy（_find_lowest_bubble 始终接收 numpy）

## 让 RTX 5070 GPU 加速真正生效的步骤（已验证）
1. `pip install "cupy-cuda12x[ctk]"` —— 拉 CUDA 12.9 toolkit headers + nvrtc wheels（支持 sm_120）
2. 无需手动设系统级 CUDA_PATH（nvidia wheels 分散布局，cuda-pathfinder 自动找）
3. gpu.py 的 probe 会通过 → xp=cupy，monitor 走 GPU 路径
4. 验证：cupy JIT 编译 kernel 成功，_analyze_region 在 sm_120 上正确工作，numpy/cupy 结果一致
