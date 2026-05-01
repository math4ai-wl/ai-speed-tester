# AI Speed Tester

一个轻量的本地 Web 工具，用来测试 OpenAI 兼容 API 中转站的流式响应速度，并辅助判断中转站节点的大致区域。

适合用来比较不同中转站、不同模型、不同网络/梯子节点下的实际体感速度。、

<img width="1920" height="993" alt="image" src="https://github.com/user-attachments/assets/b24ed6f9-b5b8-46e8-bbea-46ae7b0b8416" />

## Features

- 输入 `Base URL`、`API Key`、`Model` 后一键测试
- 统计首 token 时间和完整响应耗时
- 自动解析中转站域名对应的 IP、ASN/运营商和粗略区域
- 前端本地保存最近测速记录，支持载入、清空和复制最近结果
- `API Key` 只用于当前请求，不会写入本地状态文件
- 后端代发请求，避免浏览器 CORS 限制

## Quick Start

```bash
cd /Users/Zhuanz/ai_speed_tester
source .venv/bin/activate
python run.py
```

然后打开：

```text
http://127.0.0.1:8000
```

如果是首次安装依赖：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

## Usage

`Base URL` 可以填写：

```text
https://api.example.com
https://api.example.com/v1
```

工具会自动使用 OpenAI 兼容的 `/chat/completions` 流式接口发送一个最小测试请求。默认 prompt 是：

```text
Reply with ok.
```

点击 `开始测速并解析节点` 后，会同时得到：

- 首 token 时间
- 完整响应耗时
- HTTP 状态码
- 请求地址
- 域名解析 IP
- 国家/地区、ASN/运营商和粗略区域判断

## Metrics

`首 token 时间` 更接近用户体感里的“开始有反应要多久”。它通常受网络延迟、中转站排队、上游 API 响应速度共同影响。

`完整响应耗时` 表示整个流式响应结束用了多久。它会明显受到模型大小、输出长度、上游负载和中转站转发效率影响。

## About Proxy/VPN Node Distance

梯子节点靠近中转站服务器，通常会更快一些，因为你的电脑到中转站之间的网络往返时间更低。比如中转站 IP 大致在香港，那么使用香港或华南链路较好的节点，通常比绕到欧美节点再回来更稳。

但这不是绝对规律。最终速度还取决于：

- 你的本地网络到梯子节点的质量
- 梯子节点到中转站的链路质量
- 中转站服务器到上游模型服务的链路
- 中转站当前是否排队或限速
- 模型本身生成速度和输出长度

所以这个工具的推荐用法是：同一个 `Base URL` 和 `Model`，切换不同梯子节点后连续测几次，看首 token 和完整耗时的中位表现，而不是只看单次结果。

## Node Region Lookup

节点区域判断基于公开 IP 查询服务，结果只能作为参考。

如果中转站使用 CDN、反向代理或 Anycast，页面显示的可能是边缘节点，而不一定是真实源站位置。

## Privacy

- `API Key` 不会写入 `state.json`
- `state.json` 只保存上次填写的 `Base URL` 和 `Model`
- 测速历史保存在浏览器 `localStorage`
- IP 归属地查询会把解析到的 IP 发给公开 IP 查询服务

## Project Structure

```text
.
├── app.py
├── run.py
├── requirements.txt
└── README.md
```

## Notes

当前版本定位是本地轻量工具。后续可以继续加入多站点批量测试、结果导出、图表和部署模式。
