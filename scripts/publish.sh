#!/usr/bin/env bash
# 一键发布：质检 -> 构建校验 -> 提交 -> 推送 -> 线上验证
# 用法: bash scripts/publish.sh "提交说明"
set -euo pipefail

cd "$(dirname "$0")/.."

MSG="${1:-post: 更新内容}"

echo "== 1/5 质检 =="
python scripts/preflight.py

echo "== 2/5 构建校验 =="
if docker info >/dev/null 2>&1; then
  docker run --rm -v "$(pwd):/srv/jekyll" -w /srv/jekyll jekyll/jekyll:3.9 \
    jekyll build --destination /tmp/_site_check >/tmp/jekyll-build.log 2>&1 \
    && echo "构建通过" \
    || { echo "构建失败，详见 /tmp/jekyll-build.log"; tail -30 /tmp/jekyll-build.log; exit 1; }
else
  echo "跳过：Docker 未运行，无法做整站渲染校验（依赖 GitHub Pages 构建兜底）"
fi

echo "== 3/5 提交 =="
git add -A
if git diff --cached --quiet; then
  echo "没有变更，退出"
  exit 0
fi
git commit -m "$MSG"

echo "== 4/5 推送 =="
git push origin master

echo "== 5/5 线上验证 =="
NEW_POST=$(git show --name-only --pretty=format: HEAD | grep '^_posts/.*\.md$' | tail -1 || true)
if [ -z "$NEW_POST" ]; then
  echo "本次未新增文章，跳过页面验证"
  exit 0
fi
URL=$(python scripts/post_url.py "$NEW_POST")
echo "等待 GitHub Pages 构建：$URL"
for i in $(seq 1 30); do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -L "$URL" || echo "000")
  if [ "$CODE" = "200" ]; then
    echo "上线成功（HTTP 200）：$URL"
    exit 0
  fi
  sleep 6
done
echo "线上验证超时，请检查 GitHub Pages 构建状态："
echo "https://github.com/JimAdela/JimAdela.github.io/actions"
echo "如需回滚：git revert HEAD && git push origin master"
exit 1
