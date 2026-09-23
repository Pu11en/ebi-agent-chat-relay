---
name: release
description: ccdb のマイナー/メジャーリリース手順。「v1.4.0としてリリースして」等と言われたときに使う。
allowed-tools: Bash, Read, Edit
---

# ccdb リリース手順

## バージョン体系

```
v1.3.0  ← 手動マイナーリリース（このスキルの対象）
v1.3.1  ← PR マージごとに自動インクリメント（普段は何もしない）
v1.3.2  ← 同上
...
v1.4.0  ← 手動マイナーリリース（このスキルの対象）
```

- **自動パッチバンプ**: `AUTO_VERSION_BUMP_ENABLED=true` と `ADMIN_PAT` を設定した場合のみ、普通の PR のマージごとに `.1` ずつ自動インクリメント
- **手動リリース**: マイナー/メジャーの区切りを付けたいときに使う（このスキル）

---

## 手動リリース手順（「v1.4.0としてリリースして」）

### Step 1: リポジトリに入る

```bash
cd "$(git rev-parse --show-toplevel)"
git switch main && git pull --ff-only
```

### Step 2: ブランチを作る

```bash
git checkout -b release/v1.4.0   # バージョンは指定されたものに変える
```

### Step 3: pyproject.toml のバージョンを更新

`pyproject.toml` の `version = "1.3.x"` を指定バージョンに書き換える:

```toml
version = "1.4.0"
```

### Step 4: CHANGELOG.md を更新

1. `## [Unreleased]` セクションを `## [1.4.0] - YYYY-MM-DD` に変更（今日の日付）
2. その上に新しい空の `## [Unreleased]` セクションを追加

```markdown
## [Unreleased]

## [1.4.0] - 2026-02-22   ← 今日の日付

### Added
...（既存の Unreleased 内容がここに来る）
```

### Step 5: PR を作成（タイトルに **必ず** `[release]` を含める）

```bash
PATH="/home/ebi/.local/bin:$PATH" git add pyproject.toml CHANGELOG.md
PATH="/home/ebi/.local/bin:$PATH" git commit -m "release: v1.4.0 [release]"
git push -u origin release/v1.4.0

gh pr create \
  --base main \
  --title "release: v1.4.0 [release]" \
  --body "Release v1.4.0

## Changes
See CHANGELOG.md for details."
```

**⚠️ 重要**: 自動バージョン更新を有効にしている場合、PR タイトルに `[release]` を含めること。これがないとパッチバンプが走って v1.4.1 になってしまう。

### Step 6: 確認（数分後）

`OWNER_AUTO_MERGE_ENABLED=true` の場合は PR が自動マージされる。マージ後、
`AUTO_VERSION_BUMP_ENABLED=true` の場合はタグ `v1.4.0` と GitHub Release が作成される:

```bash
gh release view v1.4.0
```

---

## 仕組み（裏側）

```
PR マージ（auto-approve.yml）
  └── repository_dispatch: pr-merged
        └── auto-version-bump.yml
              ├── [release] あり → 現在バージョンでタグ & Release（バンプなし）
              └── [release] なし → patch++ を main に直接コミット → タグ & Release
```

- docs-sync PR（翻訳・ドキュメント更新）はバンプも Release も発生しない
- patch-bump は main への直接コミット（`enforce_admins=false` で許可）
- リリース時にはえびログ（Discord）に自動通知

---

## よくある失敗

| 状況 | 原因 | 対処 |
|------|------|------|
| PR が自動マージされない | `OWNER_AUTO_MERGE_ENABLED` が未設定 | 手動でマージするか、リポジトリ変数を `true` に設定する |
| 自動リリースが始まらない | `AUTO_VERSION_BUMP_ENABLED` または `ADMIN_PAT` が未設定 | リポジトリ変数とシークレットを設定してから再実行する |
| v1.4.1 になってしまった | PR タイトルに `[release]` がなかった | タグを削除して再度 release PR を作る |
| タグが既に存在するエラー | 同じバージョンでタグを作ろうとした | タグを削除: `gh api "repos/{owner}/{repo}/git/refs/tags/v1.4.0" --method DELETE` |
