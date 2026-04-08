# Agent Guidelines for agent-labbook

## i18n / Localization Rules

This project supports three languages. Each has specific regional conventions:

| Code | Label | Regional Standard |
|---|---|---|
| `en` | English | International English |
| `zh-hans` | 简体中文 | **中国大陆**用语习惯（Mainland China Mandarin） |
| `zh-hant` | 繁體中文 | **台灣**用語習慣（Taiwan Mandarin） |

### Key differences to observe

- **zh-hans (大陆)**: 用"应用"不用"App"、"数据库"不用"资料库"、"凭证"不用"憑證"、"密钥"不用"金鑰"、"服务器"不用"伺服器"。口语化、简洁。
- **zh-hant (台灣)**: 用"資料庫"不用"数据库"、"伺服器"不用"服务器"、"憑證"不用"凭证"、"金鑰"不用"密钥"。書面語、略正式。
- Do NOT simply convert characters between 简/繁 — the vocabulary, phrasing, and tone differ between the two regions.

### localStorage language key

Language preference is stored in `localStorage` under the key `sp-lang`, consistent with the superplanner.ai main site.

## GitHub Repository

The canonical repository URL is: `https://github.com/binbinsh/agent-labbook`

## Design System

This project follows the SuperPlanner.ai design system:

- Background: `#faf7f2` (cream)
- Ink: `#1a1410`
- Muted: `#6b5e52`
- Accent: `#c4532d` (terracotta)
- Accent hover: `#a8432a`
- Heading font: DM Serif Display (+ Noto Serif SC for zh-hans, Noto Serif TC for zh-hant)
- Body font: Inter
