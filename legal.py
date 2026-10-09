"""公开条款与隐私页面，运营者信息只作为转义后的文本显示。"""
import html
import os
from pathlib import Path
import re


PRODUCT_NAME = "欧叶OY"
TERMS_VERSION = "2026-10-09"


def _asset_version(filename):
    # 与首页共用版本，避免独立页面引用升级前的样式。
    index = Path(__file__).resolve().parent / "static" / "index.html"
    match = re.search(
        rf"/static/{re.escape(filename)}\?v=(\d+)",
        index.read_text(encoding="utf-8"),
    )
    return match.group(1) if match else "1"


def _privacy_content(operator, contact):
    return f"""
      <section aria-labelledby="privacy-operator">
        <h2 id="privacy-operator">一、运营者是谁</h2>
        <p>{PRODUCT_NAME}由{operator}运营。这份政策说明我们收集哪些数据、如何使用和保存，以及你可以怎样管理自己的数据。</p>
      </section>
      <section aria-labelledby="privacy-data">
        <h2 id="privacy-data">二、收集哪些数据</h2>
        <ul>
          <li>账号信息：用户名、邮箱、密码的哈希值和时区。我们保存密码的哈希值，不保存明文密码；注册时还记录你同意条款的时间与版本。</li>
          <li>学习数据：你记录的题目、代码、思路、错因和标签，复习记录，以及为你生成的练习题、错因聚类和薄弱点分析。</li>
          <li>笔记数据：你写的笔记（含标签、关联的题目、笔记之间的链接）、你上传到笔记里的图片，以及你在笔记里画的画板（画板内容与缩略图）。这些内容只有你本人能看到。</li>
          <li>个人资料：头像、个人简介，以及选填的“榜单显示名”。你可以决定个人简介、累计题目数和加入时间是否出现在资料卡里，也可以随时取消参与公开榜单。</li>
          <li>社区数据：论坛帖子、评论和举报，学习小组成员关系，小组每周目标的贡献统计，以及你开启时的“今日复习动态”（只显示复习数量和是否达标，不显示题目内容，仅同组成员可见，可随时关闭）。</li>
          <li>扩展与接口凭证：如果你为浏览器扩展签发了 API token，我们只保存它的哈希值，不保存明文；你可以随时重新签发或作废。</li>
          <li>提醒设置：如果你开启邮件复习提醒，我们会保存提醒开关和用于退订的凭证。</li>
          <li>交易数据：订单与支付记录，用于确认套餐、处理退款和履行财务记录要求。</li>
          <li>AI 调用日志：模型、用量、耗时等调用元数据，以及功能类型、调用时间和成功或失败状态；不记录题目、代码、对话或照片内容。</li>
        </ul>
      </section>
      <section aria-labelledby="privacy-purpose">
        <h2 id="privacy-purpose">三、这些数据用来做什么</h2>
        <p>我们用账号信息验证登录、维护账号安全和发送你需要的邮件；用学习数据提供记录、复习调度、统计和 AI 辅助学习；用社区数据提供讨论、小组协作和内容管理；用交易数据维护套餐与支付状态；用 AI 调用元数据统计用量、排查失败和核算成本。</p>
      </section>
      <section aria-labelledby="privacy-third-party">
        <h2 id="privacy-third-party">四、发给第三方的数据</h2>
        <ul>
          <li>使用 AI 功能时，相关的题目、代码、思路、错因文字会发送给站点管理员配置的 AI 模型服务商处理；拍照识别时发送的是照片。管理员未配置 AI 服务时，不会向 AI 模型服务商发送这些数据。具体服务商由站点配置决定，你可以联系运营者了解当前使用的服务商及其处理规则。</li>
          <li>支付由支付宝或微信支付处理；相应渠道接收完成交易所需的订单和支付信息。支付平台对它收集的信息适用自己的隐私规则。</li>
          <li>找回密码和复习提醒邮件通过管理员配置的 SMTP 邮件服务发送，邮件服务会处理收件邮箱和相应邮件内容。</li>
        </ul>
        <p>本站的服务器位于中华人民共和国境外（韩国首尔，使用腾讯云服务器），你的账号、学习数据和笔记保存在该服务器上。当前使用的 AI 模型服务商为 DeepSeek（以站点实际配置为准，更换时会更新本页），AI 服务商与邮件服务商也可能位于境外。使用本站即表示你知道上述数据会在境外服务器上存储和处理；请在提交内容前避免包含与学习无关的个人信息，如需了解相关安排，请联系运营者。</p>
      </section>
      <section aria-labelledby="privacy-cookie">
        <h2 id="privacy-cookie">五、Cookie 与浏览器本地偏好</h2>
        <p>本站仅使用登录会话 Cookie 来识别已登录的账号。界面风格等偏好保存在你的浏览器本地存储中。退出登录会清除当前会话 Cookie；你也可以在浏览器设置中清除本地偏好。</p>
      </section>
      <section aria-labelledby="privacy-retention">
        <h2 id="privacy-retention">六、保存期限与注销后的处理</h2>
        <p>个人学习数据通常在账号存续期间保存。注销后，我们删除你的题目、错因、复习、练习题、标签、分析数据、笔记（含笔记图片、笔记链接和画板）、头像、尚未确认的新邮箱和小组成员关系，清除邮箱、密码及会话；账号行保留为脱敏占位记录。</p>
        <p>论坛帖子、评论和举报内容以“已注销用户”的占位身份保留。订单与支付记录按财务要求保留；AI 成本记录保留，但解除与账号的关联。仍有其他成员的小组创建者需要先让成员退出或解散小组，才能注销账号。</p>
        <p>已存在的服务器备份不会因一次注销立即重写。备份默认保留约 14 份，滚动覆盖；备份中的旧数据会随保留周期滚动清除。实际清除时间取决于备份执行频率与部署的保留配置，运营者应保持配置与告知一致。</p>
      </section>
      <section aria-labelledby="privacy-rights">
        <h2 id="privacy-rights">七、你的权利与操作方式</h2>
        <p>你可以在站内导出我的数据、修改密码、用户名和邮箱，退出其他设备，或注销账号。改邮箱和注销需要验证当前密码。体验账号到期会自动清理；管理员应先撤销管理员身份，再办理自助注销。</p>
        <p>如果你希望了解、纠正或删除站内功能尚未覆盖的信息，或对数据处理有疑问，可以联系运营者。</p>
      </section>
      <section aria-labelledby="privacy-minors">
        <h2 id="privacy-minors">八、未成年人</h2>
        <p>本服务面向有民事行为能力的学习者。未成年人请在监护人同意和指导下使用；监护人对相关数据有疑问时，可以联系运营者。</p>
      </section>
      <section aria-labelledby="privacy-changes">
        <h2 id="privacy-changes">九、政策变更</h2>
        <p>数据处理方式发生变化时，我们会更新本页版本日期并在站内提示。请留意更新后的内容；需要另行取得同意的事项，应在相应处理开始前完成。</p>
      </section>
      <section aria-labelledby="privacy-contact">
        <h2 id="privacy-contact">十、联系方式</h2>
        <p>运营者：{operator}</p>
        <p>联系邮箱：{contact}</p>
      </section>"""


def _terms_content(operator, contact):
    return f"""
      <section aria-labelledby="terms-service">
        <h2 id="terms-service">一、服务说明</h2>
        <p>{PRODUCT_NAME}提供学习记录、复习、练习与讨论功能，目前处于邀请制内测阶段，面向少量邀请用户。功能、界面和可用范围可能根据内测反馈调整。运营者为{operator}。</p>
      </section>
      <section aria-labelledby="terms-account">
        <h2 id="terms-account">二、账号与安全</h2>
        <p>请遵守一人一号的约定，自己保管密码，不要把账号交给他人使用。发现账号异常时，请及时修改密码、退出其他设备并联系运营者。体验账号只用于短期体验，到期会被清理，请及时导出需要保留的数据。</p>
      </section>
      <section aria-labelledby="terms-conduct">
        <h2 id="terms-conduct">三、使用规范</h2>
        <p>讨论区不得发布违法、侵权、广告或骚扰内容；不得恶意攻击服务、冒用他人身份或干扰其他人的学习。请只上传你有权使用的内容，避免公开他人的个人信息。管理员可以删除违规内容、封禁违规账号；对处理有异议时，可以联系运营者说明情况。</p>
      </section>
      <section aria-labelledby="terms-content">
        <h2 id="terms-content">四、你的内容</h2>
        <p>你上传内容的版权仍归你或原权利人所有。为提供本服务，你授予运营者必要的存储、展示和处理许可；该许可仅用于提供相关功能，例如展示你发布的帖子、保存学习记录和处理你主动使用的 AI 功能。向 AI 服务商发送内容的说明见<a href="/privacy">隐私政策</a>。</p>
      </section>
      <section aria-labelledby="terms-ai">
        <h2 id="terms-ai">五、AI 内容仅供学习参考</h2>
        <p>AI 生成的讲解、练习题和判分可能有误，仅供学习参考，不保证正确。请结合教材、课程要求和自己的判断核对内容，不要将它直接作为考试、专业决策或其他重要事项的唯一依据。</p>
      </section>
      <section aria-labelledby="terms-payment">
        <h2 id="terms-payment">六、付费与退款</h2>
        <p>如本站提供付费套餐，套餐范围、价格、有效期与退款按站内规则及相应页面说明执行。请在购买前阅读这些说明；本条款不额外承诺超出站内规则的服务或退款条件。有支付疑问时，请提供订单信息并联系运营者。</p>
      </section>
      <section aria-labelledby="terms-changes">
        <h2 id="terms-changes">七、服务变更与终止</h2>
        <p>内测期间，服务可能因维护、配置变更或运营安排而调整、暂停或终止。对影响账号或已购买服务的重大变化，运营者会通过站内渠道告知并依照相应规则处理。你可以导出学习数据并注销账号；注销后的数据处理见隐私政策。</p>
      </section>
      <section aria-labelledby="terms-liability">
        <h2 id="terms-liability">八、责任限制</h2>
        <p>运营者会采取合理措施维护服务，但不承诺内测服务始终无中断、无错误或适合所有用途。请对重要学习资料保留自己的副本。在适用法律允许的范围内，因不可抗力或运营者无法合理控制的第三方服务故障造成的损失，按法律规定处理；本条款不排除依法不能排除的责任。</p>
      </section>
      <section aria-labelledby="terms-law">
        <h2 id="terms-law">九、适用法律与争议解决</h2>
        <p>适用法律以运营者所在地法律为准。出现争议时，请先联系运营者协商；无法协商解决的，依适用法律规定的方式处理。</p>
      </section>
      <section aria-labelledby="terms-contact">
        <h2 id="terms-contact">十、联系方式</h2>
        <p>运营者：{operator}</p>
        <p>联系邮箱：{contact}</p>
      </section>"""


def render_legal_page(kind):
    if kind not in {"terms", "privacy"}:
        raise ValueError("未知的法律页面")
    operator = html.escape(os.getenv("LEGAL_OPERATOR_NAME", "").strip() or "本站管理员")
    contact = html.escape(
        os.getenv("LEGAL_CONTACT_EMAIL", "").strip() or "请通过站内渠道联系管理员"
    )
    title = "服务条款" if kind == "terms" else "隐私政策"
    content = (
        _terms_content(operator, contact)
        if kind == "terms"
        else _privacy_content(operator, contact)
    )
    return f"""<!doctype html>
<html lang="zh-CN" data-theme="ink">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} · {PRODUCT_NAME}</title>
  <script src="/static/themes.js?v={_asset_version('themes.js')}"></script>
  <link rel="stylesheet" href="/static/style.css?v={_asset_version('style.css')}">
  <link rel="stylesheet" href="/static/legal.css?v=1">
</head>
<body class="legal-page">
  <main class="legal-document">
    <header class="legal-header">
      <a href="/">返回</a>
      <h1>{title}</h1>
      <p class="legal-meta">版本日期：<time datetime="{TERMS_VERSION}">{TERMS_VERSION}</time></p>
    </header>
    <article aria-label="{title}">{content}
    </article>
    <footer class="legal-footer">
      <nav aria-label="条款与隐私"><a href="/terms">服务条款</a><a href="/privacy">隐私政策</a></nav>
    </footer>
  </main>
</body>
</html>
"""
