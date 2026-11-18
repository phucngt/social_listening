#!/usr/bin/env bash
# Bước 0 của mỗi lần chạy: lấy lịch sử (data/, runs/) từ branch claude/* có commit mới nhất.
# Cần thiết vì mỗi lần chạy routine có thể được cấp một branch khác nhau.
set -euo pipefail
git fetch -q origin '+refs/heads/claude/*:refs/remotes/origin/claude/*'
latest=$(git for-each-ref --sort=-committerdate --format='%(refname:short)' refs/remotes/origin/claude/ | head -1)
if [ -z "$latest" ]; then
  echo "sync_state: không tìm thấy branch claude/* nào" >&2
  exit 1
fi
if git merge-base --is-ancestor "$latest" HEAD; then
  echo "sync_state: đã có lịch sử mới nhất ($latest)"
else
  git merge --no-edit -q "$latest"
  echo "sync_state: đã merge lịch sử từ $latest"
fi
