# PPO 科研基础样例来源

核查日期：2026-09-20。样例只演示资料归档、论文版本与证据定位，不构成 PPO 复现结果。

- `papers.json`：固定版本的公开元数据。`published_at` 是所选版本提交日期。
- `ppo_abstract_excerpt.txt`：来自 [PPO v2 摘要](https://arxiv.org/abs/1707.06347v2) 的五词摘录；只有摘要的一小段，没有导入 PDF 全文。Demo 以 `content_scope=abstract` 导入。
- [Deep Reinforcement Learning that Matters v3](https://arxiv.org/abs/1709.06560v3) 和 [Statistical Precipice v1](https://arxiv.org/abs/2108.13264v1)：仅登记元数据，不能为本次报告提供可定位证据。
- `synthetic_metrics.csv`：人为编写的格式 fixture，所有数值均为合成数据。两组 training seed、每组两个 evaluation episode 用于后续区分统计单位；没有运行训练、没有代表任何环境或算法性能。本阶段只把文件归档为 Artifact，不做统计。

本地文件和论文元数据由导入者声明关联。SHA-256 与页内定位只能验证归档后的内容一致性，不能认证文件确实来自发布者；全文核查和科学主张核验仍需后续完成。
