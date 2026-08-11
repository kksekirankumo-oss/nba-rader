# NBA Radar v0.7

个人自用的 NBA 新闻 / 梗灵感聚合网页。

## v0.7 最重要的变化

**Reddit 从公开 RSS 改成了正规 OAuth 接入。**

现在 Reddit 不再走匿名 RSS，而是：

- 先用 `client_credentials` 获取 Reddit OAuth token；
- 再读取 `r/nbacirclejerk`、`r/Nbamemes` 的 `new` 帖子；
- 读取 Reddit 返回的 `x-ratelimit-remaining / reset` 信息；
- 当剩余额度很低时，在网页手动抓取前给出提醒。

## 抓取模式

仍然保持 **启动抓一次，后面只手动抓**：

1. 启动 `run_windows.bat` 时抓取一次。
2. 程序运行后，只有你点击网页右上角 **“立即抓取”** 才会再次联网抓取。

网页自己每分钟刷新显示，但这只是读取本地 SQLite，不会联网抓新数据。

## 手动抓取前的提醒

点击“立即抓取”后，程序会先做一次本地检查。

### Reddit

默认设置：

```env
REDDIT_SAFE_INTERVAL_MINUTES=10
REDDIT_COOLDOWN_MINUTES=30
REDDIT_WARN_REMAINING=15
```

含义：

- **SAFE_INTERVAL**：上次刚请求过 Reddit 时，先提醒你别连点；
- **COOLDOWN**：如果 Reddit 真的回了 429，就进入冷却；
- **WARN_REMAINING**：如果 Reddit 返回的剩余额度已经很低，就提前弹提醒。

如果你点“取消”，本次会跳过 Reddit，只继续抓其他安全来源。

### YouTube

仍然会记录本机 24 小时内的搜索调用次数。默认到约 80 次开始提醒：

```env
YOUTUBE_DAILY_WARN_CALLS=80
```

## 页面内容

- **NBA 新闻**：ESPN NBA RSS、Yahoo Sports NBA RSS、YouTube 新闻。
- **NBA 梗 / 二创**：Reddit OAuth + YouTube meme/funny/reaction/edit。
- **我的收藏**：保存素材，并手动标绿灯 / 黄灯 / 红灯 / 待群内确认及风险标签。
- **抓取诊断**：显示每个来源成功、无新增、失败、冷却、未启用或安全跳过。

## Windows 升级

1. 解压 v0.7 到新文件夹。
2. 把旧版本 `.env` 复制进 v0.7。
3. 按下面的“Reddit OAuth 申请”补上新的 Reddit 参数。
4. 想保留内容、收藏和风险记录时，把旧版 `nba_radar.db` 也复制进 v0.7。
5. 双击 `run_windows.bat`。
6. 打开 `http://127.0.0.1:5050`。

## Reddit OAuth 申请（Windows 用户最简版）

1. 登录你的 Reddit 账号。
2. 打开：`https://www.reddit.com/prefs/apps`
3. 拉到最下面，点 **create another app...**
4. 推荐填写：
   - **name**：`nba-radar`
   - **type**：选 **script**
   - **description**：可空着
   - **about url**：可空着
   - **redirect uri**：填 `http://localhost:8080`
5. 创建后你会看到：
   - app 名字下面那串短字符串 = **client id**
   - `secret` 那一串 = **client secret**
6. 打开 `.env`，填入：

```env
REDDIT_CLIENT_ID=你的client_id
REDDIT_CLIENT_SECRET=你的client_secret
REDDIT_USER_AGENT=windows:nba-radar:v0.7 (by /u/你的Reddit用户名 for personal monitoring)
```

`REDDIT_USER_AGENT` 里最好带上你的 Reddit 用户名，格式保持这样最稳。

## Key

不需要 Key：ESPN NBA RSS、Yahoo NBA RSS。

可选 / 推荐：

```env
DEEPL_API_KEY=你的DeepLKey
YOUTUBE_API_KEY=你的YouTubeKey
REDDIT_CLIENT_ID=你的RedditClientId
REDDIT_CLIENT_SECRET=你的RedditClientSecret
REDDIT_USER_AGENT=windows:nba-radar:v0.7 (by /u/你的Reddit用户名 for personal monitoring)
```

## 仍然不做

- X
- Instagram / TikTok
- HTML 爬虫
- AI 自动风险定性
- 自动发布

当前目标仍然是：**发现素材 → 收藏 → 风险检查 → 再进入 brief / 视频制作。**
