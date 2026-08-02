# classifier.py
"""Content classification using Xiaomi MiMo-v2.5 API.

Analyzes post content against 16 predefined tags with sentiment,
and detects whether the post compares with other hotels.
"""
import json
import logging
import re

import requests

import config

logger = logging.getLogger(__name__)

TAGS = [
    "餐食", "茶饮", "服务", "客房面积", "美学设计",
    "客房体验", "价格", "客房用品", "会员权益", "卫生", "睡眠",
    "洗沐", "地理位置", "行业营建设计", "行业观察",
]

TAG_PROMPT_RULES = """
1. 从以下15个标签中匹配内容涉及的标签。
2. 只要内容中对某标签有明确描述、评价、事实陈述或可识别信息，就记录该标签。
3. 标签数量不限；一条内容可同时命中多个标签。
4. 不要求"只选核心主题"，而是要保留所有被明确提到的标签。
5. 程序会将返回的每个标签单独展开成一行写入表格，因此请保留全部命中的标签。
6. 如果只是极弱的泛泛带过，且无法判断具体指向，可以不标记。每个标签都需要内容中有明确的、可指认的对应描述，不要凭主题联想推断。
7. 遇到边界冲突时按以下规则裁决：
   - 仅提到"进门递毛巾+奉茶/洗尘侍茶"，但没有展开描述茶本身，记为【服务】，不记【茶饮】。
   - 房间整体外观、整体风格、全景环境、氛围颜值，记为【美学设计】。
   - 提及房型但没有提到空间状态的，记为【美学设计】。
   - 单个设施局部特写（单独拍水龙头、浴缸、花洒、洗手池、墙面、地砖等）、工程建造、建材、施工、消防、水电、隔音、通风等，记为【行业营建设计】。
   - 使用洗护产品、洗澡体感、洗浴配置与洗漱用品，记为【洗沐】；若只是单独拍浴缸/花洒/水龙头等局部物件，记为【行业营建设计】。
   - 床品、枕头、睡感、睡眠质量、睡不着、卧床舒适度，记为【睡眠】。
   - 房间内固定大件设施的使用感受，如电视、空调、智能客控、隔音、便捷度，记为【客房体验】。
   - 免费健身房、洗衣房、充电宝、欢迎水果、会员积分、会员折扣、会员专享体验，记为【会员权益】。
   - 商务办公区域、大堂、茶室、公共餐厅等共享区域的布局与造型设计，记为【美学设计】。
   - 行业营建设计和行业观察需要思考发帖人的目的：若发帖行为本身是为了吸引客户去装修，属于【行业营建设计】；任何话只要是出于消费者角度写的，都是体验类标签，不归入行业营建设计或行业观察。

【餐食】1️⃣ 涵盖门店餐厅提供的早餐、正餐、午餐、晚餐、深夜夜宵，配套甜品、时令鲜果等全品类就餐吃食（吃饭人多问题也属于餐食）。
【茶饮】2️⃣ 与茶相关的内容，如：出现了煮/泡茶过程、茶包、茶水，或文字详细描述茶叶、茶水口感、茶品品类。*如仅仅提及"进门递毛巾+奉茶（洗尘侍茶）"，没有对茶进行展开描述，则不算茶饮，需算作【服务】标签。eg：有两张拼图，上面是工作人员在泡茶的样子、下面是茶杯照片的属于茶饮。
【服务】3️⃣ 前台接待、入住退房、保洁消杀、行李寄存、叫醒服务、咨询指引、售后协助等酒店配套服务，洗尘侍茶（进门服务员给毛巾擦手和奉茶）、送餐机器人。
【客房面积】4️⃣ 标注客房实际平方数值，直观判定空间视觉观感，区分宽阔舒展、紧凑狭小两类空间状态。
【美学设计】5️⃣ 包含：(1) 装修内容——只描述房间整体外观、装潢风格、全景环境，整体颜值/氛围，主打视觉观感，如图片拍到整个房间的全貌、文字评价装修风格、环境质感好看与否。*如只拍单个设施局部特写（单独拍水龙头、浴缸、花洒），则不算装修，归为【行业营建设计】。提及房型并没有提到空间状态的也属于美学设计。(2) 公区设计内容——商务办公区域、大堂、茶室、公共餐厅等共享区域的布局与造型设计。
【客房体验】6️⃣ 描述人住在这里的实际感受，主打使用体感，如：围绕房间内固定大件设施（电视、空调、智能客控等），描述使用起来是否舒服、好用、便捷等主观住宿体验。
【价格】7️⃣ 日常挂牌订购价、平台优惠售价，区分价位档次，判定价位偏高、性价比实惠两类等级。
【客房用品】8️⃣ 保洁每日例行更换的一次性小件消耗品，宾客可无偿自行带走使用、毛巾、牙刷、拖鞋、瓶装饮品、水吧、minibar的饮用水、坚果、陈皮梨膏糖、现调风味饮料等饮品类目、茶包。
【会员权益】9️⃣ 会员专享的福利，以及酒店免费的公共配套，如：会员积分、专属房价折扣、会员特价房；免费使用的健身房、洗衣房（洗衣机、烘干机）、充电宝等；入住赠送的欢迎水果；铂金会员尊享入住即赠的「一席茶」体验；智美服务。
【卫生】🔟 评判整体洁净程度，分为环境整洁干净、杂物堆积脏乱两种卫生状况。
【睡眠】1️⃣1️⃣ 评价卧床休憩感受，如：入睡速度快、睡眠质量佳、卧床体感舒适等体验描述、床上用品。
【洗沐】1️⃣2️⃣ 洗浴配置含浴缸、淋浴花洒，配套洗发水、沐浴露等全套洗护洗漱用品。*使用洗沐产品属于洗沐，单纯只有浴缸和花洒等局部的图片内容，则属于行业营建设计。
【地理位置】1️⃣3️⃣ 明确提及门店地址（位于哪里），或讲离车站、景区、商场远近。
【行业营建设计】1️⃣4️⃣ 偏向工程建造、建材、基建、局部单品特写、门店规划（比如"怎么造的、用的什么材料"）「偏工程建材的」，如：图片只拍单个设施局部特写：单独拍浴缸、花洒、洗手池、水龙头、墙面、地砖等单件物品；聊酒店建造相关：区域划分、房间动线、水电、消防、隔音、通风、安防等基建工程；聊建材、施工、成本、门店标准化复刻、消防/卫生合规等建造运维内容。*需要思考发帖人的目的：如果发帖人的目的是吸引客户去做装修装潢，就属于行业营建设计；任何话只要是出于消费者的角度写的，都是体验类标签，不归入行业营建设计。
【行业观察】1️⃣5️⃣ 严格限定：必须是从业内人士/投资人/分析师/媒体视角进行的行业级分析内容。以下情况才算行业观察：横向对比同档位对标酒店品牌，比对客房产品、配套设施差异，参照市面售卖房价，综合分析门店市场定位与竞争优势（整篇都在对比的才是，如果从消费者角度对比，那就不是）。如：🔻市场规模与走势：营收、客房量、入住率、均价、供需变化、季度年度趋势；🔻品牌格局：连锁/单体占比、头部品牌份额、中端/高端/经济型梯队竞争；🔻客群画像：商旅、亲子、休闲、年轻群体消费偏好、出行时段、预订习惯；🔻产品业态：民宿、电竞、康养、沉浸式主题、公寓式酒店创新模式；🔻渠道流量：OTA平台、直订、政企协议、私域分销占比与转化差异；🔻成本盈利：人力、房租、能耗成本，RevPAR、毛利、回本周期；🔻政策与区位：文旅规划、商圈景区交通配套、属地管控影响；🔻消费趋势：性价比诉求、颜值体验、智能设备、卫生服务关注点；🔻风险挑战：同行内卷、客流波动、用工短缺、口碑舆情隐患；🔻未来机遇：细分赛道、跨界联名、数字化运营、本地化特色升级。*需要思考发帖人的目的：任何话只要是出于消费者的角度写的，都不属于行业观察。
⚠️ 以下情况绝不是行业观察，不要标记：(a) 消费者从用户体验角度的评论，即使提到了其他品牌名；(b) @回复、简短互动、表情评论；(c) 内容少于50字且不含行业数据/趋势/市场份额等专业分析；(d) 只是提到了品牌名但没有进行行业级分析对比；(e) 泛泛提到"酒店行业"、"中端酒店"等但无具体数据或深入分析。

对每个匹配标签判定情感倾向：
- 正面：明确夸奖全季大观，或发布看好大观的观点。判定标准——内容中**明确使用了正面评价词**（如"喜欢"、"好评"、"太棒了"、"满意"、"漂亮"、"好看"、"好吃"、"舒服"+"具体维度"），或语气是明确赞许的（如"幸福感爆棚"、"细节到位"、"惊艳"、"无可挑剔"）。
- 中性：只是客观描述、信息陈述，或有优有劣但不明显偏向。
- 负面：提到了明确劣势或负面评价。

【情感判定的关键——区分"赞美 vs 描述"】
正面情感的核心是「评价性表达」，即作者带有明确的赞许/喜爱语气；中性是「描述性表达」，即作者只是在陈述事实或风格但不带评价色彩。具体规则：
1. **明确正面词 + 具体维度 → 正面**：例如"床很舒服"、"早餐好吃"、"装修好看"、"服务周到"、"环境真不错"。
2. **明确赞许语气（无需具体维度）→ 正面**：例如"幸福感爆棚"、"细节有做的非常到位"、"惊艳"、"无可挑剔"、"很整洁"、"简约高级"。"高级"/"好高级"/"高级感"等涉及格调质感的表述，指向【美学设计】。
3. **风格描述（无评价词）→ 中性**：例如"中式美学"、"禅意"、"侘寂风"、"宋氏美学"。
4. **模糊短词（少于5字、无指向）→ 通常不标标签**：例如"舒适"、"美"、"舒适感拉满"、"惬意"——这些词太模糊，无法判断指向哪个维度，应保持空标签。
5. **感叹但非评价 → 中性或不标**：例如"太牛了"、"这家酒店也太智能了吧"——是对新奇事物的感叹，不是正面体验评价。

【情感保守原则】
- 当情感倾向不明显、作者语气含糊时，优先标"中性"。
- 但当内容**明确使用**赞许词或评价性表达时，应标"正面"，不要过度保守。
- 短评的情感保守仅适用于"无明确指向"的情况，对明确带有赞许词的短评（如"喜欢"、"漂亮"）仍应标正面。

核心判断原则（边界裁决）：
【行业营建设计和行业观察这两类需要思考发帖人的目的，如果他的目的是吸引去装修，就属于行业营建设计，任何话只要是出于消费者的角度写的，都是体验类标签，不归入行业营建设计或行业观察。】
"""

COMPARISON_PROMPT_RULES = """
判断内容是否提到了除"全季大观"以外的其他酒店品牌。重点关注以下品牌是否被提及：
亚朵、萨和、见野、竹居、锦江系（锦江、锦江之星、7天、维也纳、丽枫、希岸）、万豪、希尔顿、洲际（IHG、假日、智选假日、皇冠假日）、如家、汉庭、全季（非大观店）、雅高、华住、首旅如家、格林豪泰、宜必思、桔子、凯悦、香格里拉、温德姆、喜来登、凯宾斯基、丽思卡尔顿、四季、柏悦、W酒店、JW万豪、康莱德、城际、美居、宋品、晓青山、瑞吉、傲途格、索菲特、铂尔曼。
严格要求：必须看到具体品牌名称才算对比。以下情况不算对比，标记"否"：
- "睡过上百家豪华酒店""住过很多酒店""各种酒店都住过"等泛指，没有提到具体品牌名。
- "豪华酒店""中端酒店""酒店行业""同档位品牌"等泛指。
- "华住集团"是全季大观的母公司；提到"华住"或"华住集团"不算作与其他酒店品牌的对比。
只有明确提到了上面列表中的具体品牌名称，或其他非"全季大观"的具体酒店品牌，才标记"是"。
"""

JSON_RESPONSE_RULES = """
请严格返回以下 JSON 格式，不要添加任何其他内容：
{
    "tags": ["标签-情感", "标签-情感"],
    "has_comparison": "是或否"
}
"""

SYSTEM_PROMPT = f"""你是一个酒店行业内容分析专家。分析用户发布的帖子内容，完成以下任务：

{TAG_PROMPT_RULES}
{COMPARISON_PROMPT_RULES}
{JSON_RESPONSE_RULES}

示例：
输入："全季大观的早餐很丰盛，但房间隔音一般，隔壁是亚朵"
输出：{{"tags": ["餐食-正面", "客房体验-负面"], "has_comparison": "是"}}

输入："全季大观的床垫很软，枕头很高睡不着"
输出：{{"tags": ["睡眠-负面"], "has_comparison": "否"}}

输入："和亚朵对比性价比差，但装修确实好看"
输出：{{"tags": ["价格-负面", "美学设计-正面"], "has_comparison": "是"}}

输入："行业观察：中端酒店市场持续增长，华住系份额扩大"
输出：{{"tags": ["行业观察-中性"], "has_comparison": "否"}}

输入："全季大观的房间很大有60平，位置离地铁站很近"
输出：{{"tags": ["客房面积-正面", "地理位置-正面"], "has_comparison": "否"}}

输入："全季大观的服务很周到，早餐也好吃，装修大气"
输出：{{"tags": ["服务-正面", "餐食-正面", "美学设计-正面"], "has_comparison": "否"}}

输入："早餐不错房间大离地铁近"
输出：{{"tags": ["餐食-正面", "客房面积-正面", "地理位置-正面"], "has_comparison": "否"}}

输入："进门先递毛巾再奉茶，服务细节很好"
输出：{{"tags": ["服务-正面"], "has_comparison": "否"}}

输入："茶室里泡了一壶龙井，茶香四溢，口感回甘"
输出：{{"tags": ["茶饮-正面"], "has_comparison": "否"}}

输入："工作人员泡茶的样子很专业，茶杯也很精致"
输出：{{"tags": ["茶饮-正面"], "has_comparison": "否"}}

输入："全季大观的洗护用品很好用，枕头也舒服，卫生间干净"
输出：{{"tags": ["洗沐-正面", "睡眠-正面", "卫生-正面"], "has_comparison": "否"}}

输入："有点宋氏美学"
输出：{{"tags": ["美学设计-中性"], "has_comparison": "否"}}

输入："出差人最重要的就是晚上能休息好啦"
输出：{{"tags": [], "has_comparison": "否"}}

输入："被你拍的太美啦"
输出：{{"tags": [], "has_comparison": "否"}}

输入："床垫不是那种柔软的 硬度可 床品不厚重"
输出：{{"tags": ["睡眠-中性"], "has_comparison": "否"}}

输入："一杯茶静坐细品"
输出：{{"tags": ["茶饮-中性"], "has_comparison": "否"}}

输入："浴缸的特写，水龙头很亮"
输出：{{"tags": ["行业营建设计-中性"], "has_comparison": "否"}}

输入："整个房间的装修风格很雅致，氛围感拉满"
输出：{{"tags": ["美学设计-正面"], "has_comparison": "否"}}

输入："华住集团中端酒店市场份额分析"
输出：{{"tags": ["行业观察-中性"], "has_comparison": "否"}}

输入："不如住城际"
输出：{{"tags": [], "has_comparison": "是"}}

输入："@刀切大馒头 你也去住了？"
输出：{{"tags": [], "has_comparison": "否"}}

输入："和亚朵对比，全季大观的房间更大更舒服"
输出：{{"tags": ["客房面积-正面", "客房体验-正面"], "has_comparison": "是"}}

输入："拍的好美"
输出：{{"tags": [], "has_comparison": "否"}}

输入："小姐姐也漂亮"
输出：{{"tags": [], "has_comparison": "否"}}

输入："全季都给你拍得这么高级"
输出：{{"tags": [], "has_comparison": "否"}}

输入："拍的质感 感觉好高级"
输出：{{"tags": [], "has_comparison": "否"}}

输入："全季这么好看了嘛"
输出：{{"tags": [], "has_comparison": "否"}}

输入："去吃！宝珠奶酪！"
输出：{{"tags": [], "has_comparison": "否"}}

输入："想吃酸萝卜"
输出：{{"tags": [], "has_comparison": "否"}}

输入："感觉这个酸奶碗好好吃"
输出：{{"tags": [], "has_comparison": "否"}}

输入："光看工作人员漂不漂亮去了"
输出：{{"tags": [], "has_comparison": "否"}}

输入："这家酒店也太智能了吧"
输出：{{"tags": ["客房体验-中性"], "has_comparison": "否"}}

输入："一键呼叫前台，这也太放便了吧，想冲了"
输出：{{"tags": ["客房体验-中性", "服务-中性"], "has_comparison": "否"}}

输入："禅意满满啊"
输出：{{"tags": ["美学设计-中性"], "has_comparison": "否"}}

输入："冥想好去处"
输出：{{"tags": ["美学设计-中性"], "has_comparison": "否"}}

输入："好有设计感的酒店"
输出：{{"tags": ["美学设计-正面"], "has_comparison": "否"}}

输入："美的"
输出：{{"tags": ["美学设计-正面"], "has_comparison": "否"}}

输入："很整洁的"
输出：{{"tags": ["卫生-正面"], "has_comparison": "否"}}

输入："全屋智能，美食精致，幸福感爆棚"
输出：{{"tags": ["客房体验-正面", "餐食-正面"], "has_comparison": "否"}}

输入："简约高级，细节有做的非常到位"
输出：{{"tags": ["美学设计-正面"], "has_comparison": "否"}}

输入："舒适"
输出：{{"tags": [], "has_comparison": "否"}}

输入："舒适感拉满"
输出：{{"tags": [], "has_comparison": "否"}}

输入："惬意住了"
输出：{{"tags": [], "has_comparison": "否"}}

输入："惊喜住了"
输出：{{"tags": [], "has_comparison": "否"}}

输入："开心快乐"
输出：{{"tags": [], "has_comparison": "否"}}

输入："大观系列真的种草"
输出：{{"tags": [], "has_comparison": "否"}}

输入："住的都没舍得出去探店"
输出：{{"tags": [], "has_comparison": "否"}}

输入："回复 红鲤鱼与绿鲤鱼与俞 : 就舍不得出去系列"
输出：{{"tags": [], "has_comparison": "否"}}

输入："未来最好品牌"
输出：{{"tags": [], "has_comparison": "否"}}

输入："这家酒店不错"
输出：{{"tags": [], "has_comparison": "否"}}

输入："高级"
输出：{{"tags": ["美学设计-正面"], "has_comparison": "否"}}

输入："感觉这个酸奶碗好好吃"
输出：{{"tags": [], "has_comparison": "否"}}

输入："超级喜欢全季的枕头"
输出：{{"tags": ["睡眠-正面"], "has_comparison": "否"}}

输入："喜欢全季酒店的早餐"
输出：{{"tags": ["餐食-正面"], "has_comparison": "否"}}

核心判断原则：
1. 评价对象原则：所有标签都是对「全季大观」酒店的评价。
   - 夸赞照片/拍摄质感/博主本人（"拍的好美"、"小姐姐漂亮"、"拍得高级"）是对图片/人的赞美，不是对酒店的评价，不标标签。
   - 表达食欲/想去吃（"想吃"、"去吃"、"馋"）如果未描述在酒店的实际用餐体验，不标餐食。
   - 仅提及城市名、表达想去/要来，不构成对酒店的评价。
2. 描述≠评价原则：描述风格（宋氏美学、侘寂风等）不等于正面评价，除非伴随明确的正面词（好看、漂亮、喜欢等）。纯风格描述标为中性。
3. 需求≠体验原则：表达"什么很重要"、"需要什么"不等于"酒店提供了好的体验"。必须有对酒店实际体验的描述才算评价。
4. 不过度标注原则：一条评论只标注评论内容明确涉及的标签，不做上下文推断。泛泛夸赞（"不错"、"很棒"）不标具体标签。
5. 短评保守原则：对于短评（尤其是少于15字），必须包含明确指向酒店某个具体维度（如"早餐好吃"、"床舒服"、"房间大"）才能标注标签和正面情感。以下短评不标任何标签：
   - 纯感叹/感叹词："开心快乐"、"惊喜住了"、"惬意住了"、"太牛了"、"好的"
   - 泛泛赞美无指向："好美"、"真的很不错"、"很舒服"（没说哪里好/舒服）
   - 表达意愿/计划："下次就住这家"、"列入行程了"、"要列入行程了吗"
   - 与人互动："是哦就是要放松"、"你值得拥有"、"我也喜欢"
   - 回复他人且无实质评价内容："噶被你种草了"、"本来想骑着去西湖的"
6. 中性优先原则：当情感倾向不明显时，优先标"中性"而非"正面"。但内容**明确使用赞许词或评价性表达**时（如"喜欢"、"漂亮"、"好看"、"幸福感爆棚"、"细节到位"、"惊艳"）应标正面，不要过度保守。
7. 【仅适用于正文/长内容】全面覆盖原则：长正文（正文/帖子）通常涉及5-10个标签，请逐段检查，不要遗漏任何明确涉及的标签。对正文的标签召回要充分，宁可多标也不要漏标。"""


HOTEL_BRANDS = [
    "亚朵", "萨和", "见野", "竹居",
    "锦江", "锦江之星", "7天", "维也纳", "丽枫", "希岸",
    "万豪", "希尔顿", "洲际", "IHG", "假日", "智选假日", "皇冠假日",
    "如家", "汉庭", "全季", "雅高", "华住", "首旅如家", "格林豪泰",
    "宜必思", "桔子", "桔子水晶", "凯悦", "香格里拉", "温德姆",
    "喜来登", "凯宾斯基", "丽思卡尔顿", "四季", "柏悦", "W酒店",
    "JW万豪", "康莱德", "城际", "美居", "宋品", "晓青山",
    "喜来登", "瑞吉", "傲途格", "索菲特", "铂尔曼",
]


def detect_hotel_brands(text: str) -> list[str]:
    """Local keyword detection for hotel brands (supplements LLM detection).

    Skips "全季" when "全季大观" is the main subject.
    Skips "华住" when "全季大观" is present (parent company, not a comparison).
    """
    found = []
    has_target = "全季大观" in text
    for brand in HOTEL_BRANDS:
        if brand in text:
            if brand == "全季" and has_target:
                continue
            if brand == "华住" and has_target:
                continue
            found.append(brand)
    return found


# ── Short comment pre-processing ──

# Patterns for meaningless short comments (skip API call entirely)
_MEANINGLESS_PATTERNS = [
    re.compile(r"大观探索(发现|发展)官"),  # marketing campaign signups
    re.compile(r"^报名"),                   # signup comments
]

# Affirmation-only / social chat / generic positive words (no classification value)
_AFFIRMATION_WORDS = {
    # 原有
    "好的", "是的", "对的", "阔以", "是嘟", "哈哈", "嗯", "嗯嗯",
    "确实", "同意", "哇塞", "太好啦", "好", "啊", "哦",
    # 社交闲聊
    "wo", "八错～", "不是哈哈哈哈", "羡慕", "好会选", "是滴",
    "对啊", "对呢", "[亲一个R]", "哇", "开心", "难得清闲", "陶冶下",
    "没有吧", "不是哦 是单独的一个", "广子吗",
    # 通用正面词（人工标注标签=/，返回空与人工一致）
    "不错", "不错诶", "不错不错", "真不错", "真不错啊", "这个真不错",
    "舒服", "很舒服", "好舒服", "蛮好", "真好啊",
    "好牛", "感觉好牛", "很棒", "他们家真的很棒",
    "蛮有创意", "挺实用的", "好有特色",
    "绝了[棒R]", "这波我给💯", "拍得好好啊！",
    # 提问/意向（人工标注标签=/，可不走API）
    "哪里呀", "这是哪家酒店呀？", "需要提前预约吗", "这是哪家", "额 这是哪家",
    "想去体验诶！", "想去住一下", "想去这里看看", "好想去看看",
    "@Rachel 我也想去", "有机会真的要去住。", "和朋友一起去", "想去的哈哈哈",
    "下次要来就住这里", "哇塞收藏了下次去", "是我期待的呢",
    "下次去杭州就来这家！",
    # 通用表达
    "哇 很舒服的感觉", "这个可以", "出行必选", "谢谢鉴赏",
    "杭州 全季大观",
    # 通用夸赞（无具体指向，不标标签）
    "看着不错", "看着还可以啊", "哇！看着很棒",
    "小姐姐品味真棒", "小姐姐品味太棒了",
    "美评", "谢谢美赞", "好有魅力呀。",
    # 问位置（由地理位置本地规则处理）
    "在哪呀", "在哪里啊", "蹲蹲地址呀",
}

# Style description words (描述风格 ≠ 正面评价，纯风格描述 → 中性)
_STYLE_WORDS = {
    "宋氏美学", "侘寂风", "极简风", "工业风", "新中式",
    "日式风", "北欧风", "ins风", "复古风", "现代风",
    "中式", "和风", "法式", "美式", "英伦风",
}

# Explicit positive words that indicate real evaluation (not just description)
_EXPLICIT_POSITIVE = re.compile(
    r"好|美|棒|舒服|舒适|赞|喜欢|满意|推荐|惊艳|绝|nice|优秀|给力|"
    r"周到|贴心|细致|暖心|感动|惊喜|超值|完美|顶级|奢华|高级|不错|"
    r"香|甘|拉满|丰盛|好吃|可口|美味|鲜|甜|软|大|宽敞|干净|整洁|近|方便|"
    r"精致|震撼|治愈|高级感|氛围感|质感|好看|漂亮|大气|雅致|"
    r"封神|宝藏|神仙|yyds|绝绝子|爱了|心动|种草|安利|打卡|出片|"
    r"温馨|惬意|享受|放松|安心|舒适感|品质|格调|用心|细节"
)
# Explicit negative words that indicate real dissatisfaction (keep conservative)
_EXPLICIT_NEGATIVE = re.compile(
    r"差|脏|吵|失望|差评|糟糕|垃圾|坑|难受|恶心|破|丑|土|不行|不好|不值"
)


def _correct_descriptive_sentiment(content: str, tags: list[str], is_post_content: bool = False) -> list[str]:
    """Post-process: downgrade 正面→中性 for purely descriptive content.

    If the content has no explicit positive/negative evaluation words,
    but API returned 正面, downgrade to 中性 (description ≠ evaluation).

    For post content (正文), relax the correction: long-form content with
    detailed descriptions often implies positive evaluation even without
    explicit keywords.
    """
    has_positive_word = bool(_EXPLICIT_POSITIVE.search(content))
    has_negative_word = bool(_EXPLICIT_NEGATIVE.search(content))

    # If content has explicit evaluation words, keep API sentiment as-is
    if has_positive_word or has_negative_word:
        return tags

    # Post content (正文): don't downgrade if it's detailed enough
    # Long-form descriptions of hotel features generally imply positive sentiment
    if is_post_content and len(content) > 80:
        return tags

    # No explicit evaluation words → downgrade 正面 to 中性
    corrected = []
    for tag_str in tags:
        parts = tag_str.rsplit("-", 1)
        if len(parts) == 2 and parts[1] == "正面":
            corrected.append(f"{parts[0]}-中性")
        else:
            corrected.append(tag_str)
    return corrected


# Reply prefix pattern: "回复 username : content"
_REPLY_PREFIX = re.compile(r"^回复\s+.+\s*:\s*")


def _strip_reply_prefix(text: str) -> str:
    """Remove '回复 xxx :' prefix and return the actual reply content."""
    return _REPLY_PREFIX.sub("", text).strip()


def _is_meaningless_short(text: str) -> bool:
    """Check if a short comment is meaningless for classification."""
    text = text.strip()
    if not text:
        return True
    # Check meaningless patterns
    for pattern in _MEANINGLESS_PATTERNS:
        if pattern.search(text):
            return True
    # Check affirmation-only words
    if text in _AFFIRMATION_WORDS:
        return True
    return False


def _append_unique_tag(tags: list[str], tag: str, sentiment: str) -> None:
    """Keep inferred fallback tags stable and deduplicated."""
    tag_str = f"{tag}-{sentiment}"
    if tag_str not in tags:
        tags.append(tag_str)


def _classify_short_content_locally(text: str) -> dict:
    """Fallback heuristic for short comments when the LLM returns no tags."""
    content = text.strip()
    tags: list[str] = []

    # 餐食：需有实际用餐/食物描述，排除"想吃/去吃/馋"等纯食欲表达
    if re.search(r"早餐|早饭|午餐|晚餐|宵夜|夜宵|好吃|丰盛|鲜果", content) \
            or (re.search(r"吃", content) and not re.search(r"^想吃|^去吃|^馋|想吃|去吃|嘴馋", content)):
        sentiment = "负面" if re.search(r"难吃|不好吃|一般", content) else "正面"
        _append_unique_tag(tags, "餐食", sentiment)

    if re.search(r"煮茶|泡茶|茶包|茶香|茶味|茶水|茶好喝|龙井|普洱|铁观音|茶壶|茶杯|茶室|品茶|茶艺", content):
        sentiment = "负面" if re.search(r"苦|涩|淡|难喝", content) else "正面"
        _append_unique_tag(tags, "茶饮", sentiment)

    if re.search(r"服务|前台|接待|退房|入住|机器人|奉茶|洗尘侍茶", content):
        sentiment = "负面" if re.search(r"差|慢|冷漠|一般", content) else "正面"
        _append_unique_tag(tags, "服务", sentiment)

    if re.search(r"\d+\s*平|房间大|空间大|宽敞|开阔", content):
        _append_unique_tag(tags, "客房面积", "正面")
    elif re.search(r"房间小|狭小|局促|拥挤", content):
        _append_unique_tag(tags, "客房面积", "负面")

    if re.search(r"装修|好看|漂亮|大气|颜值|氛围|美|雅|治愈|高级", content):
        sentiment = "负面" if re.search(r"丑|老旧|土", content) else "正面"
        _append_unique_tag(tags, "美学设计", sentiment)

    if re.search(r"大堂|茶室|公区|公共餐厅", content) and re.search(r"设计|好看|漂亮|大气|美", content):
        _append_unique_tag(tags, "美学设计", "正面")

    if re.search(r"空调|电视|智能客控|客控|隔音|方便|便捷", content):
        sentiment = "负面" if re.search(r"差|吵|不方便|不好用|一般", content) else "正面"
        _append_unique_tag(tags, "客房体验", sentiment)

    # 舒适/住宿感受类表达（人工认为应标客房体验-正面）
    if re.search(r"舒适|舒适感|住得(好|舒服)|舍不得出去|住着舒服|住宿体验", content) \
            and not re.search(r"差|不好|一般", content):
        _append_unique_tag(tags, "客房体验", "正面")

    if re.search(r"贵|涨价|不值", content):
        _append_unique_tag(tags, "价格", "负面")
    elif re.search(r"便宜|划算|实惠|性价比高", content):
        _append_unique_tag(tags, "价格", "正面")

    if re.search(r"毛巾|牙刷|拖鞋|瓶装水|瓶装饮品|水吧|minibar|饮用水|坚果|梨膏糖|风味饮料|茶包", content):
        sentiment = "负面" if re.search(r"差|少|一般", content) else "正面"
        _append_unique_tag(tags, "客房用品", sentiment)

    if re.search(r"会员|积分|折扣|特价房|健身房|洗衣房|烘干机|充电宝|欢迎水果|一席茶", content):
        sentiment = "负面" if re.search(r"没有|取消|少", content) else "正面"
        _append_unique_tag(tags, "会员权益", sentiment)

    if re.search(r"干净|整洁", content):
        _append_unique_tag(tags, "卫生", "正面")
    elif re.search(r"脏|乱|不卫生", content):
        _append_unique_tag(tags, "卫生", "负面")

    if re.search(r"床|枕头|睡|睡眠", content):
        sentiment = "负面" if re.search(r"睡不着|失眠|吵|硬|高", content) else "正面"
        _append_unique_tag(tags, "睡眠", sentiment)

    if re.search(r"浴缸|花洒|洗发水|沐浴露|洗护|洗澡|洗漱", content):
        sentiment = "负面" if re.search(r"差|小|一般", content) else "正面"
        _append_unique_tag(tags, "洗沐", sentiment)

    if re.search(r"地铁|车站|景区|商场|位置|近|方便", content):
        sentiment = "负面" if re.search(r"远|偏|不方便", content) else "正面"
        _append_unique_tag(tags, "地理位置", sentiment)

    if re.search(r"水龙头|洗手池|地砖|墙面|建材|施工|消防|水电|通风|安防", content):
        _append_unique_tag(tags, "行业营建设计", "中性")

    # 风格描述情感降级：纯风格描述词（无正面/负面伴随）→ 中性
    has_style = any(w in content for w in _STYLE_WORDS)
    if has_style and not tags:
        _append_unique_tag(tags, "美学设计", "中性")

    local_brands = detect_hotel_brands(content)
    has_comparison = "是" if local_brands else "否"

    return {"tags": tags, "has_comparison": has_comparison}


SHORT_COMMENT_PROMPT = """你是一个酒店行业内容分析专家。当前分析的是一条非常简短的用户评论（少于15个字）。

虽然内容很短，但仍需尽力判断其涉及的标签。只要内容中对某标签有明确描述，就记录该标签；标签数量不限。
短评优先根据高信号关键词判断，例如：
- "好看"/"漂亮"/"大气"/"美"/"雅"/"治愈" → 常见于【美学设计】
- "早餐"/"宵夜"/"好吃"/"丰盛" → 常见于【餐食】
- "床"/"枕头"/"睡不着"/"睡得好" → 常见于【睡眠】
- "贵"/"便宜"/"划算"/"性价比" → 常见于【价格】
- "服务好"/"周到"/"前台" → 常见于【服务】
- "干净"/"脏"/"乱" → 常见于【卫生】
- 提到其他酒店品牌名 → `has_comparison` 通常为"是"

请按以下规则判断：
""" + TAG_PROMPT_RULES + """
""" + COMPARISON_PROMPT_RULES + """

如果内容确实无法匹配任何标签，返回空数组即可。

核心判断原则：
1. 评价对象原则：所有标签都是对「全季大观」酒店的评价。仅提及城市名、表达想去/要来，不构成对酒店的评价。
2. 描述≠评价原则：描述风格（宋氏美学、侘寂风等）不等于正面评价，除非伴随明确的正面词（好看、漂亮、喜欢等）。纯风格描述标为中性。
3. 需求≠体验原则：表达"什么很重要"、"需要什么"不等于"酒店提供了好的体验"。必须有对酒店实际体验的描述才算评价。
4. 不过度标注原则：一条评论只标注评论内容明确涉及的标签，不做上下文推断。泛泛夸赞（"不错"、"很棒"）不标具体标签。
5. 短评保守原则：对于短评（尤其是少于15字），必须包含明确指向酒店某个具体维度（如"早餐好吃"、"床舒服"、"房间大"）才能标注标签和正面情感。以下短评不标任何标签：
   - 纯感叹/感叹词："开心快乐"、"惊喜住了"、"惬意住了"、"太牛了"、"好的"
   - 泛泛赞美无指向："好美"、"真的很不错"、"很舒服"（没说哪里好/舒服）
   - 表达意愿/计划："下次就住这家"、"列入行程了"
   - 与人互动："是哦就是要放松"、"你值得拥有"、"我也喜欢"
6. 中性优先原则：当情感倾向不明显时，优先标"中性"而非"正面"。只有当内容明确包含正面评价词且指向具体维度时才标正面。

请严格返回 JSON 格式：
{"tags": ["标签-情感", "标签-情感"], "has_comparison": "是或否"}

示例：
输入："好看"  → {"tags": ["美学设计-正面"], "has_comparison": "否"}
输入："美"  → {"tags": ["美学设计-正面"], "has_comparison": "否"}
输入："美的"  → {"tags": ["美学设计-正面"], "has_comparison": "否"}
输入："好有设计感的酒店"  → {"tags": ["美学设计-正面"], "has_comparison": "否"}
输入："很整洁的"  → {"tags": ["卫生-正面"], "has_comparison": "否"}
输入："贵"  → {"tags": ["价格-负面"], "has_comparison": "否"}
输入："早餐不错房间大离地铁近"  → {"tags": ["餐食-正面", "客房面积-正面", "地理位置-正面"], "has_comparison": "否"}
输入："萨和好"  → {"tags": ["行业观察-中性"], "has_comparison": "是"}
输入："好的"  → {"tags": [], "has_comparison": "否"}
输入："有点宋氏美学"  → {"tags": ["美学设计-中性"], "has_comparison": "否"}
输入："禅意满满啊"  → {"tags": ["美学设计-中性"], "has_comparison": "否"}
输入："冥想好去处"  → {"tags": ["美学设计-中性"], "has_comparison": "否"}
输入："出差人最重要的就是晚上能休息好啦"  → {"tags": [], "has_comparison": "否"}
输入："被你拍的太美啦"  → {"tags": [], "has_comparison": "否"}
输入："床垫不是那种柔软的 硬度可 床品不厚重"  → {"tags": ["睡眠-中性"], "has_comparison": "否"}
输入："一杯茶静坐细品"  → {"tags": ["茶饮-中性"], "has_comparison": "否"}
输入："舒适"  → {"tags": [], "has_comparison": "否"}
输入："高级"  → {"tags": ["美学设计-正面"], "has_comparison": "否"}
输入:"好高级"  → {"tags": ["美学设计-正面"], "has_comparison": "否"}
输入："舒适感拉满"  → {"tags": [], "has_comparison": "否"}
输入："惬意住了"  → {"tags": [], "has_comparison": "否"}
输入："惊喜住了"  → {"tags": [], "has_comparison": "否"}
输入："开心快乐"  → {"tags": [], "has_comparison": "否"}
输入："太牛了哥哥"  → {"tags": [], "has_comparison": "否"}
输入："吃"  → {"tags": [], "has_comparison": "否"}
输入："喜欢全季的枕头"  → {"tags": ["睡眠-正面"], "has_comparison": "否"}
输入："喜欢全季酒店的早餐"  → {"tags": ["餐食-正面"], "has_comparison": "否"}
输入："感觉这个酸奶碗好好吃"  → {"tags": [], "has_comparison": "否"}
输入："想吃酸萝卜"  → {"tags": [], "has_comparison": "否"}
输入："光看工作人员漂不漂亮去了"  → {"tags": [], "has_comparison": "否"}"""


def build_user_prompt(post_content: str) -> str:
    """Build the user message for the API call."""
    return f"请分析以下帖子内容：\n\n{post_content}"


def _extract_json_object(text: str) -> str:
    """从模型响应里抠第一个完整 JSON 对象，容忍 markdown 围栏和尾部多余文本。

    MiMo 即便设了 response_format=json_object，偶尔仍返回 ```json 围栏
    或在 JSON 后追加说明文字，导致 json.loads 报 "Extra data"。
    """
    text = (text or "").strip()
    # 剥掉 markdown 代码围栏（```json ... ``` 或 ``` ... ```）
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # 抠第一个平衡的 {...}，忽略其后多余文本（处理 "Extra data"）
    start = text.find("{")
    if start == -1:
        return text  # 没有 {，交给 json.loads 报错
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return text  # 没找到闭合，原样交给 json.loads


def parse_response(response_text: str) -> dict:
    """Parse the API response JSON into structured result.

    Returns:
        {"tags": ["睡眠-正面", ...], "has_comparison": "是"} or
        {"tags": [], "has_comparison": "否"} on failure
    """
    default_result = {"tags": [], "has_comparison": "否"}

    try:
        text = _extract_json_object(response_text)
        result = json.loads(text)

        # Validate structure
        if "tags" not in result or "has_comparison" not in result:
            logger.warning(f"[classifier] Missing keys in response: {result}")
            return default_result

        # Validate tags format
        valid_tags = []
        for tag_str in result["tags"]:
            parts = tag_str.rsplit("-", 1)
            if len(parts) == 2 and parts[0] in TAGS and parts[1] in ("正面", "中性", "负面"):
                valid_tags.append(tag_str)
            else:
                logger.warning(f"[classifier] Invalid tag format: {tag_str}")

        return {
            "tags": valid_tags,
            "has_comparison": "是" if result["has_comparison"] == "是" else "否",
        }

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error(f"[classifier] Failed to parse response: {e}")
        logger.warning(f"[classifier] raw response (first 300): {response_text[:300]!r}")
        return default_result


def classify_content(post_content: str, retries: int = 0, is_post_content: bool = False) -> dict:
    """Classify post content using MiMo-v2.5 API.

    Args:
        post_content: The post text to classify.
        retries: Number of retries on empty/failed response. Defaults to 0
            (no retry — fails fast to fallback).
        is_post_content: True if the content is a long-form post (正文).

    Returns:
        {"tags": ["睡眠-正面", ...], "has_comparison": "是/否"}
    """
    if not config.MIMO_API_KEY:
        logger.error("[classifier] MIMO_API_KEY not set")
        return {"tags": [], "has_comparison": "否"}

    if not post_content or not post_content.strip():
        return {"tags": [], "has_comparison": "否"}

    # Step 1: Strip reply prefix and check for meaningless content
    content = _strip_reply_prefix(post_content.strip())
    if not content:
        return {"tags": [], "has_comparison": "否"}

    if _is_meaningless_short(content):
        return {"tags": [], "has_comparison": "否"}

    # Step 2: Use short comment prompt for brief content
    is_short = len(content) < 15
    system_prompt = SHORT_COMMENT_PROMPT if is_short else SYSTEM_PROMPT

    # Very long post content (>2000 chars) tends to trigger empty/unstable API
    # responses. Truncate to keep the most information-dense portion.
    # Hotel review posts are usually front-loaded, so keep the head.
    api_content = content
    if is_post_content and len(content) > 2000:
        api_content = content[:2000]
        logger.info(f"[classifier] Truncated long post {len(content)} -> 2000 chars")

    # Long post content — ensure enough output room for many tags
    max_tokens = 1024 if is_post_content else 500

    default_result = {"tags": [], "has_comparison": "否"}
    last_result = default_result
    for attempt in range(retries + 1):
        try:
            response = requests.post(
                f"{config.MIMO_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {config.MIMO_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": config.MIMO_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": build_user_prompt(api_content)},
                    ],
                    "temperature": 0.1,
                    "max_tokens": max_tokens,
                    "response_format": {"type": "json_object"},
                },
                timeout=10,
            )
            response.raise_for_status()

            data = response.json()
            resp_content = data["choices"][0]["message"]["content"]
            result = parse_response(resp_content)

            # If we got tags, return immediately; otherwise retry
            if result["tags"]:
                # Post-process: correct descriptive sentiment
                result["tags"] = _correct_descriptive_sentiment(content, result["tags"], is_post_content)
                # Supplement with local brand detection
                local_brands = detect_hotel_brands(content)
                if local_brands and result["has_comparison"] == "否":
                    result["has_comparison"] = "是"
                return result

            # Empty response: back off then retry (exponential, capped at 2s)
            if attempt < retries:
                backoff = min(0.5 * (2 ** attempt), 2.0)
                logger.warning(
                    f"[classifier] Empty response on attempt {attempt+1}, "
                    f"retrying in {backoff}s..."
                )
                import time as _time
                _time.sleep(backoff)
                continue

        except requests.RequestException as e:
            logger.error(f"[classifier] API call failed (attempt {attempt+1}): {e}")
            if attempt < retries:
                import time as _time
                _time.sleep(min(0.5 * (2 ** attempt), 2.0))
                continue
        except (KeyError, IndexError) as e:
            logger.error(f"[classifier] Unexpected response structure: {e}")
            if attempt < retries:
                continue

    # All retries exhausted, supplement with local brand detection
    if is_short:
        fallback_result = _classify_short_content_locally(content)
        if fallback_result["tags"]:
            return fallback_result

    local_brands = detect_hotel_brands(content)
    if local_brands:
        last_result["has_comparison"] = "是"
    return last_result


def classify_content_expanded(post_content: str, is_post_content: bool = False) -> list[dict]:
    """Classify and expand into one entry per tag.

    Returns:
        [{"tag": "睡眠", "sentiment": "正面", "has_comparison": "是"}, ...]
        Empty list if no tags matched.
    """
    raw = classify_content(post_content, is_post_content=is_post_content)
    if not raw["tags"]:
        return []
    expanded = []
    for tag_str in raw["tags"]:
        parts = tag_str.rsplit("-", 1)
        if len(parts) == 2:
            expanded.append({
                "tag": parts[0],
                "sentiment": parts[1],
                "has_comparison": raw["has_comparison"],
            })
    return expanded
