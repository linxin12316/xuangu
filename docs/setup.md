# 详细配置教程

## 第一步：拿 Server 酱 SendKey

1. 浏览器打开 https://sct.ftqq.com
2. 点页面右上角「登录」→ 用微信扫码授权
3. 登录后页面会自动跳到「SendKey」管理页
4. 你会看到一个形如 `SCT123456ABCDEF...` 的字符串，**点复制**

> 💡 Server 酱免费版每天有 5 条消息额度，够用了（咱们一天只推 2 条）。

## 第二步：关注微信公众号

在 Server 酱页面会让你扫码关注「方糖」公众号——必须关注，否则收不到消息。

## 第三步：在仓库填 Secret

1. 浏览器打开 https://github.com/linxin12316/xuangu/settings/secrets/actions
   - 如果 404，说明你还没登录 GitHub 或者仓库还没建好
2. 点右上角绿色按钮 **New repository secret**
3. 表单填：
   - Name: `SCKEY`（必须大写，必须完全一样）
   - Secret: 粘贴第一步复制的 SendKey
4. 点 **Add secret**

## 第四步：手动触发一次

1. 浏览器打开 https://github.com/linxin12316/xuangu/actions
2. 左侧菜单点 **Daily Stock Pick**
3. 右侧出现 **Run workflow** 下拉按钮，点开 → 点绿色 **Run workflow**
4. 页面会刷新，约 30 秒后会出现一行新记录（黄色圆圈 = 运行中）
5. 等 2-5 分钟变成绿勾 ✅
6. 检查微信：方糖公众号应该推送了一条「📈 选股报告 yyyy-mm-dd」

## 如果失败了怎么办

### 微信没收到推送

- 在 Actions 页面点击那次运行，展开 `Run pick` 步骤的日志
- 查找 `❌ 推送失败` 或 `❌ 推送异常`
- 常见原因：
  - SendKey 填错了 → 重新去 sct.ftqq.com 复制
  - 没关注方糖公众号 → 去微信关注
  - 当日推送超额 → 等明天

### Action 整体失败

- 多半是 akshare 接口在 GitHub 美国机房被拦
- 解决：在仓库里点 **Run workflow** 重试，通常第二三次会成功
- 长期解决：在 `requirements.txt` 锁定一个稳定的 akshare 版本

### 不在交易日

- 节假日和周末脚本会自动跳过，不会推送
- 这是正常行为，不是 bug

## 修改推送时间

打开 `.github/workflows/daily-pick.yml`，找到这行：

```yaml
- cron: '30 0 * * 1-5'   # UTC，对应北京 08:30
```

cron 五个字段：分 时 日 月 周。注意 GitHub Actions 用 **UTC 时间**，北京时间要 -8 小时。

例如想改成北京 09:00 早 → UTC 01:00 → `'0 1 * * 1-5'`

## 撤销 Token

完成上述配置后，去 https://github.com/settings/tokens 把那个 `ghp_ePxz4...` 撤销，重新生成一个新的（如果以后还要让 AI 帮你管理仓库的话）。
