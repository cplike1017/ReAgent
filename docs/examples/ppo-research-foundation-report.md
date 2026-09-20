# PPO 单智能体复现：资料与证据基础样例

研究问题：开展 PPO 复现前，哪些方法细节、评估协议和统计单位仍需核对？

项目：project_89c1c29963ec4323b8d86301f01685ff

## 当前证据边界

训练尚未执行；本报告只导出登记的来源、定位与待核验主张。
locator_verified 仅表示摘录存在于资料中，不表示已验证科学结论。
selected_pages 表示曾返回该页的文本片段，不表示读完该页或全文。

## 论文版本

- Proximal Policy Optimization Algorithms (arxiv:1707.06347, v2)
  - version_id: version_6ba40d6fc01b41c8a78446e1430b8c63
  - 来源：https://arxiv.org/abs/1707.06347v2
  - 资料范围：abstract；读取：selected_pages；页：[1]
- Deep Reinforcement Learning that Matters (arxiv:1709.06560, v3)
  - version_id: version_7043b70f8ae245e195ff76d7b753f39b
  - 来源：https://arxiv.org/abs/1709.06560v3
  - 资料范围：metadata；读取：metadata；页：[]
- Deep Reinforcement Learning at the Edge of the Statistical Precipice (arxiv:2108.13264, v1)
  - version_id: version_ef36cd6d52c9418c989bb4bf8b92f055
  - 来源：https://arxiv.org/abs/2108.13264v1
  - 资料范围：metadata；读取：metadata；页：[]

## 主张（尚未完成语义核验）

- [fact / unverified] PPO 摘要提到对小批量数据进行多轮更新；尚需阅读全文核对实现与超参数。
  - claim_id: claim_69c07f5def914cabaaa5407d56697835
  - supports: evidence_9d3b24312d8e4f99ab8405c58f9cc8b8
- [hypothesis / unverified] 待检验：固定评估协议后，不同独立训练 seed 的 PPO 结果仍可能存在差异。
  - claim_id: claim_48f5787e5f9c413f82a2de34939050ff
  - 缺少证据
- [fact / unverified] synthetic_metrics.csv 是合成格式样例，尚未计算统计量，不能用于判断 PPO 性能。
  - claim_id: claim_d32338d976ba4d2ab6d0f1caba2b7569
  - 缺少证据

## 原文定位

- evidence_9d3b24312d8e4f99ab8405c58f9cc8b8 → version_6ba40d6fc01b41c8a78446e1430b8c63
  - text_document: 1; normalized chars: 0:36
  - SHA-256: 08197509c703e5ed0a96570c04060a519dcf4c5eb808023f4c42020964dba36b; parser: utf8/whitespace-v1
  - 摘录：multiple epochs of minibatch updates

## 产物清单

- artifact_5b6a071df9ec4d07beb0a34468a18dbf: ppo_abstract_excerpt.txt; 37 bytes; SHA-256: 08197509c703e5ed0a96570c04060a519dcf4c5eb808023f4c42020964dba36b
- artifact_05efc89038fc47ba81ab57c8ec0c51d2: synthetic_metrics.csv; 223 bytes; SHA-256: a11cad7be6bdcfd93570a3ac240980b6dfb8f1d956a928215a0cb9aaa27e7b6d

## 下一步

核对论文全文与实验协议；在独立训练记录接入后再分析实验结果。
