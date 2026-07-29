# 问题梳理

---

## 问题：从零开始用 Git 管理本地项目并上传 GitHub 的流程是什么？

**解决方案：**

1. `git init` — 在项目目录初始化本地仓库（创建隐藏的 `.git` 文件夹，相当于装上"监控摄像头"）
2. 创建 `.gitignore` — 指定哪些文件/文件夹不上传（大数据文件、缓存、输出文件等）
3. `git add .` — 将文件加入暂存区（可以理解为购物车）
4. `git commit -m "说明"` — 本地提交（结账，正式写入版本历史）
5. 在 GitHub 网页创建空仓库（**不要**勾选 README、.gitignore、License，否则会冲突）
6. `git remote add origin <仓库地址>` — 让本地仓库知道远程仓库的位置
7. `git push -u origin master` — 把本地 master 分支推上去。

**日常修改后只需三步：** `git add .` → `git commit -m "xxx"` → `git push`

---

## 问题：master、origin、origin/master 分别是什么？

| 术语 | 归属 | 含义 |
|---|---|---|
| `master` | 本地 | 本地默认主分支，commit 直接写入这里 |
| `origin` | 配置项 | GitHub 远程仓库地址的别名（方便书写） |
| `origin/master` | 本地缓存 | 远程仓库状态的本地快照，用于离线对比 |

---

## 问题：origin/master 有什么用？为什么不直接对比 origin 和 master？

**原因：** Git 的设计哲学是"尽量离线能干活"。如果每次 `git status` 都要联网去 GitHub 查询，断网就无法工作，且增加网络延迟和服务器压力。

**做法：** Git 在本地存一份远程状态的快照（`origin/master`），只在 `git fetch` / `git push` / `git pull` 时才联网同步。

**核心作用：** 作为对比的参照物，让你随时能判断"我超前了"还是"我落后了"，无需联网。

---

## 问题：commit → push 的完整数据流向是什么？

```
写代码 → git add → git commit → master（直接写入，origin/master 不参与）
                                   │
                               git push
                                   │
                                   ↓
                        origin/master 同步更新（只是标签挪动，不是中转站）
                                   │
                                   ↓
                          GitHub 远程仓库（真正上传）
```

**关键点：**
- commit 永远是本地操作，和远程无关
- push 的数据直接从 master 到 GitHub，不经过 origin/master
- push 成功后，git 顺手把 origin/master 标签挪到最新位置

---

## 问题：多人协作时，commit 后应该先 pull 还是先 push？

**正确流程：**

```
git pull          # 1. 先把别人的改动拉下来（fetch 更新 origin/master + merge 合并进 master）
                  #    有冲突就先手动解决
git add .         # 2. 加入自己的改动
git commit -m ""  # 3. 提交
git push          # 4. 推上去
```

**建议养成习惯：先 pull 再 commit**，这样可以避免生成多余的 merge commit，保持提交历史是一条干净的直线。如果先 commit 再 pull，虽然也能工作，但会产生无意义的合并记录。
