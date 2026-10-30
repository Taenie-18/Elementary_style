"""Generate the initial field configuration from the supplied loan complaint.

Run with Python 3.12+. Validation additionally requires jsonschema>=4.18.
This is a configuration builder, not a model extraction service.
"""
import copy
import json
from collections import Counter
from pathlib import Path
from zipfile import ZipFile
from lxml import etree

OUT = Path(__file__).resolve().parent
SOURCE = next((OUT.parent / 'upload').glob('（民间借贷示例*'))
NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
with ZipFile(SOURCE) as z:
    root = etree.fromstring(z.read('word/document.xml'))
    table = root.find('w:body/w:tbl', NS)
    ROWS = [''.join(r.xpath('.//w:t/text()', namespaces=NS)) for r in table.findall('w:tr', NS)]
    ROW_LABELS = [''.join(r.findall('w:tc', NS)[0].xpath('.//w:t/text()', namespaces=NS)).strip() for r in table.findall('w:tr', NS)]
    ROW_LABELS[10] = '诉讼请求原文'
    ROW_LABELS[23] = '事实与理由原文'

def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def obj(properties, description='', row=None, module=None):
    d = {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
    if description: d['description'] = description
    if row is not None: d['x-source-row'] = row
    if module: d['x-module'] = module
    return d

def field(kind, description, **kw):
    d = {'description': description + ' 仅依据输入文本；未提及或无法可靠确定时返回 null。'}
    if kind.startswith('enum:'):
        d['$ref'] = '#/$defs/' + kind.split(':', 1)[1]
    elif kind == 'money':
        d.update(type=['string', 'null'], pattern=r'^(0|[1-9][0-9]*)\.[0-9]{2}$')
    elif kind == 'decimal':
        d.update(type=['string', 'null'], pattern=r'^(0|[1-9][0-9]*)(\.[0-9]+)?$')
    elif kind == 'date':
        d.update(type=['string', 'null'], format='date', pattern=r'^\d{4}-\d{2}-\d{2}$')
    else:
        d.update(type=['string', 'null'], minLength=1)
    d.update(kw)
    return d

def arr(items, description, **kw):
    d = {'type': ['array', 'null'], 'items': items, 'minItems': 1, 'description': description}
    d.update(kw)
    return d

def choices(name, description):
    return arr({'enum': GROUPS[name]['values']}, description + ' 同组集中存储，去重；未提供选择时为 null，不用空数组。', uniqueItems=True, **{'x-enum-group': name})

def names(description):
    return arr({'type': 'string', 'minLength': 1}, description + ' 未提及时为 null。', uniqueItems=True)

def conditional_none(d, selector, value, children):
    d.setdefault('allOf', []).append({
        'if': {'properties': {selector: {'const': value}}, 'required': [selector]},
        'then': {'properties': {k: {'type': 'null'} for k in children}}
    })
    return d

def other_detail(d, selector, child):
    d.setdefault('allOf', []).append({
        'if': {'properties': {child: {'type': 'string'}}, 'required': [child]},
        'then': {'properties': {selector: {'const': '其他'}}}
    })
    return d

def array_other_detail(d, selector, child):
    d.setdefault('allOf', []).append({
        'if': {'properties': {child: {'type': 'string'}}, 'required': [child]},
        'then': {'properties': {selector: {'type': 'array', 'contains': {'const': '其他'}}}}
    })
    return d

GROUPS = {
    'yesNo': {'values': ['是', '否'], 'selection': 'single', 'description': '是否类单选；空白不等于否。'},
    'hasNone': {'values': ['有', '无'], 'selection': 'single', 'description': '有无类单选；未勾选不等于无。'},
    'sex': {'values': ['男', '女'], 'selection': 'single', 'description': '模板列出的性别选项；不从姓名或证件号码推导。'},
    'orgType': {'values': ['有限责任公司', '股份有限公司', '上市公司', '其他企业法人', '事业单位', '社会团体', '基金会', '社会服务机构', '机关法人', '农村集体经济组织法人', '城镇农村的合作经济组织法人', '基层群众性自治组织法人', '个人独资企业', '合伙企业', '不具有法人资格的专业服务机构'], 'selection': 'multiple', 'description': '保留模板全部15项；组织形式与上市状态可能同时出现，首版按同组多选。'},
    'ownership': {'values': ['国有', '民营', '其他'], 'selection': 'single', 'description': '所有制主选项；其他说明分开存储。单选为初步业务约定，需确认。'},
    'stateOwnershipType': {'values': ['控股', '参股'], 'selection': 'multiple', 'description': '国有之下的子选项；首版按多选保存，是否应互斥需业务确认。'},
    'authorization': {'values': ['一般授权', '特别授权'], 'selection': 'single', 'description': '同一代理记录的代理权限按单选处理。'},
    'acceleration': {'values': ['提前还款（加速到期）', '解除合同'], 'selection': 'multiple', 'description': '模板以斜杠分隔，是否允许并选未明；首版多选，进入待确认清单。'},
    'deliveryMethod': {'values': ['现金', '转账', '其他'], 'selection': 'multiple', 'description': '允许多次交付采用多种方式；其他说明关联其他选项。'},
    'repaymentMethod': {'values': ['到期一次性还本付息', '按月计息、到期一次性还本', '按季计息、到期一次性还本', '按年计息、到期一次性还本', '其他'], 'selection': 'multiple', 'description': '模板未声明互斥，首版保留多选；同一笔借款出现互斥安排时进入业务复核。'},
    'ratePeriod': {'values': ['年', '季', '月'], 'selection': 'single', 'description': '利率周期，不能默认为年。'},
    'registrationType': {'values': ['正式登记', '预告登记'], 'selection': 'multiple', 'description': '模板登记子选项，可能对应多担保物；首版多选，需确认单个担保物建模要求。'},
    'guaranteeType': {'values': ['一般保证', '连带责任保证'], 'selection': 'single', 'description': '单份保证合同的保证方式按单选处理；不同合同需后续扩展为重复记录。'},
    'understanding': {'values': ['了解', '不了解'], 'selection': 'single', 'description': '每个告知事项分别取值，不能由某一项推及其他项。'},
    'mediationIntent': {'values': ['是', '否', '暂不确定，想要了解更多内容'], 'selection': 'single', 'description': '保留调解意愿的三个选项。'}
}
DEFS = {k: {'type': ['string', 'null'], 'enum': v['values'] + [None], 'description': v['description']} for k, v in GROUPS.items() if v['selection'] == 'single'}

natural = obj({
    'name': field('text', '当前自然人姓名；与当前角色及当前记录绑定。'),
    'sex': field('enum:sex', '当前自然人性别；仅依据明确文字或可靠勾选。'),
    'birthDate': field('date', '当前自然人明确提供的完整出生日期，标准化为 YYYY-MM-DD；不从身份证反推。'),
    'ethnicity': field('text', '当前自然人民族，保留原文。'),
    'workUnit': field('text', '当前自然人工作单位，不能使用对方单位补齐。'),
    'position': field('text', '当前自然人在工作单位中的职务。'),
    'phone': field('text', '当前自然人联系电话；兼容既有接口为字符串，多号码保留原文分隔顺序，不转换为数字。'),
    'residence': field('text', '当前自然人的住所地即户籍所在地；不得与经常居住地混用。'),
    'habitualResidence': field('text', '当前自然人的经常居住地；不得仅复制住所地。'),
    'certificateType': field('text', '证件类型；只提取原文明示名称。'),
    'certificateNumber': field('text', '证件号码，保留字符串、前导零及末尾字母；不自行补全。')
}, '自然人数据结构；所有属性都必须出现，缺失值为 null。')
organization = obj({
    'name': field('text', '当前法人或非法人组织的名称。'),
    'residence': field('text', '组织住所地或主要办事机构所在地；不同于注册地。'),
    'registrationPlace': field('text', '组织注册地或登记地；不从住所地自动复制。'),
    'legalRep': field('text', '法定代表人或主要负责人姓名；不是委托诉讼代理人。'),
    'position': field('text', '法定代表人或主要负责人的职务。'),
    'phone': field('text', '组织栏内联系电话，兼容既有接口保存字符串，不默认归为代表人私人号码。'),
    'creditCode': field('text', '统一社会信用代码，保留原始字符串，不能生成或修复缺失位。'),
    'type': choices('orgType', '组织类型；只存已选或明确表述的选项，不逐项输出布尔值。'),
    'ownership': field('enum:ownership', '组织所有制主选项；如其他集体所有制，主值其他、说明集体所有制。'),
    'ownershipOther': field('text', '所有制其他的附加说明；仅在 ownership 为其他时允许非空。'),
    'stateOwnershipType': choices('stateOwnershipType', '国有项下控股、参股子选项；仅 ownership 为国有时允许非空。')
}, '法人和非法人组织的数据结构。')
other_detail(organization, 'ownership', 'ownershipOther')
organization['allOf'].append({'if': {'properties': {'stateOwnershipType': {'type': 'array'}}, 'required': ['stateOwnershipType']}, 'then': {'properties': {'ownership': {'const': '国有'}}}})
DEFS['naturalPersonData'] = natural
DEFS['organizationData'] = organization
DEFS['party'] = {'oneOf': [
    obj({'category': {'const': '自然人'}, 'data': {'$ref': '#/$defs/naturalPersonData'}}),
    obj({'category': {'const': '法人、非法人组织'}, 'data': {'$ref': '#/$defs/organizationData'}})
], 'description': '按category选择唯一data结构，不输出空白模板占位主体。'}

agents = obj({
    '是否委托': field('enum:hasNone', '是否委托诉讼代理人；有、无均未选且无明确文字时为 null。'),
    '代理人': arr(obj({
        'name': field('text', '本条委托诉讼代理人的姓名。'),
        'workUnit': field('text', '本条代理人的单位。'),
        'position': field('text', '本条代理人的职务。'),
        'phone': field('text', '本条代理人的联系电话，多个号码保留字符串。'),
        'authorization': field('enum:authorization', '本条代理人的一般授权或特别授权；不能由代理人职业推断。')
    }), '仅输出实际出现的代理人；委托人关联保存在内部主体索引及审核信息，不增加到正式字段中。')
}, '代理人总状态和重复记录。', row=4, module='parties')
conditional_none(agents, '是否委托', '无', ['代理人'])

claims = obj({
    '诉讼请求原文': field('text', '诉讼请求自由文本区完整内容，保留编号、括号及费用条件；不自行重写。', **{'x-source-row': 10}),
    '1.本金': obj({
        '截至日期': field('date', '诉请尚欠本金的统计截止日期。'),
        '尚欠本金': field('money', '诉请尚欠本金，不能直接使用历史借款交付总额替代。'),
        '币种': field('text', '本金币种，仅依据明确人民币、外币等文字。')
    }, row=11),
    '2.利息': obj({
        '截至日期': field('date', '尚欠或暂计利息的统计日期。'),
        '尚欠利息': field('money', '原文明示的利息金额，不按利率重新计算替换。'),
        '计算方式': field('text', '完整保留利息基数、标准、起止及暂计条件。'),
        '计算基数': field('money', '诉请计算利息采用的本金基数，需明示。'),
        '利率标准': field('text', '诉请利息采用的文字标准，如一年期贷款市场报价利率；不查询外部数值。'),
        '起算日期': field('date', '诉请利息明确起算日期。'),
        '暂计截止日期': field('date', '利息暂计截止日期，不代表最终停止计息。'),
        '是否请求支付至实际清偿之日止': field('enum:yesNo', '利息是否请求至实际清偿之日；可依据勾选或明确诉请。')
    }, row=12),
    '3.是否要求提前还款或解除合同': obj({
        '是否要求': field('enum:yesNo', '是否要求提前还款或解除合同，不能由已逾期推定。'),
        '请求类型': choices('acceleration', '本项下提前还款与解除合同选项。')
    }, row=13),
    '4.是否主张担保权利': obj({'是否主张': field('enum:yesNo', '是否主张担保权利；不能从未填写担保信息直接推定否。'), '内容': field('text', '主张担保权利的具体内容。')}, row=14),
    '5.是否主张实现债权的费用': obj({'是否主张': field('enum:yesNo', '是否请求实现债权的费用。'), '明细': field('text', '费用明细及条件，保留如有等限定，不生成未提供金额。')}, row=15),
    '6.是否主张诉讼费用': field('enum:yesNo', '是否请求诉讼费用；不由第5项自动复制。', **{'x-source-row': 16}),
    '7.其他请求': field('text', '本栏明确填写的其他请求；不得把已有本金和利息重复放入。', **{'x-source-row': 17}),
    '8.标的总额': obj({'金额': field('money', '原文明示标的总额；计算检查只告警，不改写。'), '暂计截止日期': field('date', '标的总额标注的暂计日期。')}, row=18)
}, '诉讼请求；保留原有1至8项编号。', module='claims')
conditional_none(claims['properties']['3.是否要求提前还款或解除合同'], '是否要求', '否', ['请求类型'])
for key in ['4.是否主张担保权利', '5.是否主张实现债权的费用']:
    conditional_none(claims['properties'][key], '是否主张', '否', [k for k in claims['properties'][key]['properties'] if k != '是否主张'])

jurisdiction = obj({
    '1.有无仲裁、法院管辖约定': obj({'有无约定': field('enum:hasNone', '有无仲裁或法院管辖约定；双方都未选时为 null，不依据无书面合同推定。'), '合同条款及内容': field('text', '仲裁或管辖条款原文及内容，不自行推荐法院。')}, row=20),
    '2.是否已经诉前保全': obj({'是否已经诉前保全': field('enum:yesNo', '只指已经发生的诉前保全，不能从拟申请保全或费用如有推定。'), '保全法院': field('text', '已实施诉前保全的法院。'), '保全时间': field('date', '已实施诉前保全的完整日期；仅有年月时保留在审核原文。'), '保全案号': field('text', '诉前保全案号，保留括号及字母。')}, row=21)
}, '约定管辖及已经实施的诉前保全。', module='remaining')
conditional_none(jurisdiction['properties']['1.有无仲裁、法院管辖约定'], '有无约定', '无', ['合同条款及内容'])
conditional_none(jurisdiction['properties']['2.是否已经诉前保全'], '是否已经诉前保全', '否', ['保全法院', '保全时间', '保全案号'])

facts = obj({
    '事实与理由原文': field('text', '事实与理由自由文本完整原文，保留当事人关系、承诺及催收表述。', **{'x-source-row': 23}),
    '1.合同的签订情况（名称、编号、签订时间、地点等）': obj({'签订情况原文': field('text', '本栏合同签订情况原文，如无书面合同；不能改写为不存在借贷合同。'), '合同名称': field('text', '明确提供的合同名称；模板文书标题不是借款合同名称。'), '合同编号': field('text', '明确提供的合同编号。'), '签订时间': field('date', '合同明确签订日期，不能用转账日期替代。'), '签订地点': field('text', '合同明确签订地点，不能用当事人住址替代。')}, row=24),
    '2.签订主体': obj({'出借人': names('明确的出借人名称，借助主体索引识别代称，不把原告身份一律等同出借人。'), '借款人': names('明确的借款人名称，不把被告身份一律等同借款人。')}, row=25),
    '3.借款金额': obj({'约定': field('money', '明确约定的借款金额；不能仅凭实际转账额补齐。'), '实际提供': field('money', '实际交付的借款金额，可依据事实原文中的明确转账。'), '提供方式': choices('deliveryMethod', '借款实际提供方式，可依据明确叙述，不限于本栏勾选。'), '其他说明': field('text', '提供方式为其他时对应的说明。')}, row=26),
    '4.借款期限': obj({'是否到期': field('enum:yesNo', '借款是否到期，可依据现还款期限已过等明确表述；不依赖运行当天推算。'), '约定起始日期': field('date', '约定借款期限的完整起始日期，不能以交付日期替代。'), '约定截止日期': field('date', '约定借款期限的完整截止日期；仅年月、月底等相对表述首版不补成日。'), '期限原文': field('text', '保留约定期限、不完整日期或月底前等承诺原文。')}, row=27),
    '5.借款利率': obj({'利率': field('decimal', '合同约定利率的百分数数值，例如年利率12%保存12；不填诉请LPR标准。'), '周期': field('enum:ratePeriod', '约定利率周期，年季月不能猜测。'), '合同条款编号': field('text', '约定利率条款的条号。'), '补充内容': field('text', '借款利率的补充说明，例如借期内不计息、逾期利率标准、利率调整方式等；仅依据原文，未提及为 null。')}, row=28),
    '6.借款提供时间': arr(obj({'提供日期': field('date', '该笔借款实际交付日期，可依据事实段明确转账日期。'), '金额': field('money', '本次借款交付金额，不混用尚欠本金。')}), '按每次明确交付建立记录，未提及时 null；重复记录属于可复制栏位的结构化扩展。', **{'x-source-row': 29}),
    '7.还款方式': obj({'方式': choices('repaymentMethod', '合同明确约定的还款方式；不得由归还承诺推定一次还本付息。'), '其他说明': field('text', '其他还款方式的对应说明。')}, row=30),
    '8.还款情况': obj({'已还本金': field('money', '明确已偿还本金；尚欠等于交付额也不能自动填0。'), '已还利息': field('money', '明确已付利息；未记载不能填0。'), '还息至': field('date', '明确已付利息覆盖至的完整日期。')}, row=31),
    '9.是否存在逾期还款': obj({'是否存在': field('enum:yesNo', '是否存在逾期还款，依据本栏或明确事实。'), '逾期起始日期': field('date', '明确逾期起始日；不能仅以诉请利息起算日替代。'), '逾期时长原文': field('text', '明确的逾期时长描述；表格固定提示至今已逾期不是填写值。')}, row=32),
    '10.是否签订物的担保（抵押、质押）合同': obj({'是否签订': field('enum:yesNo', '是否签订物的担保合同，依据明确否定或勾选。'), '签订时间': field('date', '物的担保合同完整签订日期。')}, row=33),
    '11.担保人、担保物': obj({'担保人': names('明确的物的担保人名称，不把空白栏识别成主体。'), '担保物': names('明确的担保物描述，首版不拆出未在模板列明的权属证号等字段。')}, row=34),
    '12.是否最高额担保（抵押、质押）': obj({'是否最高额担保': field('enum:yesNo', '是否最高额抵押或质押担保。'), '担保债权的确定时间': field('date', '明确担保债权确定日期，不用签订日期替代。'), '担保额度': field('money', '明确最高额担保额度。')}, row=35),
    '13.是否办理抵押、质押登记': obj({'是否办理': field('enum:yesNo', '是否办理抵押或质押登记。'), '登记类型': choices('registrationType', '正式登记、预告登记选项。')}, row=36),
    '14.是否签订保证合同': obj({'是否签订': field('enum:yesNo', '是否签订保证合同。'), '签订时间': field('date', '保证合同完整签订日期。'), '保证人': names('保证合同中的保证人名称，不能与物的担保人自动合并。'), '主要内容': field('text', '保证合同主要内容原文。'), '保证方式': field('enum:guaranteeType', '单份保证合同的一般保证或连带责任保证，不自行法律推定。')}, row=37),
    '15.其他担保方式': obj({'是否存在': field('enum:yesNo', '是否存在其他担保方式。'), '形式': field('text', '其他担保的明确形式。'), '签订时间': field('date', '其他担保的完整签订日期。')}, row=38),
    '16.其他需要说明的内容（可另附页）': field('text', '本栏或明确标注附件中的其他说明，不重复自动生成事实摘要。', **{'x-source-row': 39}),
    '17.请求依据': obj({'合同约定': field('text', '原文提出的合同约定依据；没有书面合同不等于本字段自动为无。'), '法律规定': field('text', '仅转录原文援引的法规条文；不补全、不验证其当前法律适用。')}, row=40),
    '18.证据清单（可另附页）': field('text', '只转录正式证据清单或明确列明的附件；事实中提到录音不自动生成证据名称和编号。', **{'x-source-row': 41})
}, '事实与理由；保留原有1至18项编号。', module='facts')
array_other_detail(facts['properties']['3.借款金额'], '提供方式', '其他说明')
array_other_detail(facts['properties']['7.还款方式'], '方式', '其他说明')
for key, selector in [('9.是否存在逾期还款','是否存在'), ('10.是否签订物的担保（抵押、质押）合同','是否签订'), ('12.是否最高额担保（抵押、质押）','是否最高额担保'), ('13.是否办理抵押、质押登记','是否办理'), ('14.是否签订保证合同','是否签订'), ('15.其他担保方式','是否存在')]:
    conditional_none(facts['properties'][key], selector, '否', [k for k in facts['properties'][key]['properties'] if k != selector])

benefits = [
    ('1.调解启动与诉讼排期', '立案后选择先行调解的，可以很快启动调解程序。如不同意调解，法院将依程序开庭审理案件，但可能需要经过较长一段时间的排期等待，且审理、执行周期相对较长。'),
    ('2.调解相关诉讼费用', '选择先行调解，调解成功且自动履行的免交诉讼费用，申请司法确认的不交纳诉讼费用，要求出具调解书的减半交纳诉讼费用。'),
    ('3.更换调解组织和调解员', '首次调解不成功，但仍有继续调解意愿的，可以选择更换调解组织和调解员再进行调解。调解无法达成一致意见的，法院将依程序排期开庭。'),
    ('4.调解保密性', '依照法律规定，调解具有保密性要求，调解过程不公开，调解协议未经当事人同意不得公开。'),
    ('5.调解协议效力', '调解达成的协议具有法律效力，可以依照法律规定申请司法确认，具有强制执行效力。')
]
mediation = obj({
    '是否了解调解作为非诉讼纠纷解决方式，能及时、高效、低成本、不伤和气地解决纠纷': field('enum:understanding', '本项对调解的了解程度；仅提取选择，固定告知文字不代表本人选择。', **{'x-source-row': 43}),
    '是否了解先行调解解决纠纷的好处': obj({k: field('enum:understanding', '独立提取本项了解程度。告知原文：' + text, **{'x-key-status': 'proposed_summary_key', 'x-source-label': text}) for k, text in benefits}, row=44),
    '是否考虑先行调解': field('enum:mediationIntent', '是否考虑先行调解；不从了解选项反推。', **{'x-source-row': 45})
}, '七个独立选择组；告知文字仅作为模板标签，不作为法律结论验证。', module='remaining')

PROPS = {role: arr({'$ref': '#/$defs/party'}, role + '实际出现的主体记录；模板空白自然人或组织栏不是一条记录。', **{'x-source-row': rows, 'x-module': 'parties'}) for role, rows in [('原告',[2,3]),('被告',[5,6]),('第三人',[7,8])]}
PROPS['委托诉讼代理人'] = agents
PROPS['诉讼请求'] = claims
PROPS['约定管辖和诉前保全'] = jurisdiction
PROPS['事实与理由'] = facts
PROPS['对纠纷解决方式的意愿'] = mediation
PROPS['签署信息'] = obj({'具状人': names('仅提取具状人签字盖章位置实际填写的名称；不能从原告姓名自动补齐。'), '日期': field('date', '仅提取落款实际日期；不能用制文日期或运行当天日期。')}, '签署位置为空时各值为 null。', module='remaining')
SCHEMA = obj(PROPS, '民间借贷要素式起诉状正式字段初版；结构对象固定存在，叶子及重复记录可为null。')
SCHEMA.update({'$schema':'https://json-schema.org/draft/2020-12/schema', '$id':'urn:yaosushi:loan-complaint:v0.1', 'title':'民间借贷要素式起诉状字段初版', '$defs':DEFS})

# Expand role-specific configurations while keeping category branches separate.
FIELDS = []
def pointer_escape(s): return s.replace('~','~0').replace('/','~1')
def walk(node, path, spath, module=None, row=None, category=None):
    module=node.get('x-module',module); row=node.get('x-source-row',row)
    if path == '/事实与理由/5.借款利率/补充内容':
        return
    if '$ref' in node:
        target=node['$ref'].split('/')[-1]
        merged={**DEFS[target], **{k:v for k,v in node.items() if k != '$ref'}}
        return walk(merged,path,node['$ref'],module,row,category)
    if 'oneOf' in node:
        for i, branch in enumerate(node['oneOf']):
            cat=branch['properties']['category']['const']
            walk(branch,path,spath+'/oneOf/'+str(i),module,row,cat)
        return
    if 'properties' in node:
        for k,v in node['properties'].items():
            walk(v,path+'/'+pointer_escape(k),spath+'/properties/'+pointer_escape(k),module,row,category)
        return
    if node.get('items') and ('properties' in node['items'] or '$ref' in node['items']):
        return walk(node['items'],path+'/*',spath+'/items',module,row,category)
    if 'const' in node: return
    source_rows=row if isinstance(row,list) else ([row] if row is not None else [])
    if category and source_rows and path.startswith(('/原告/','/被告/','/第三人/')):
        source_rows=[source_rows[0 if category=='自然人' else -1]]
    typ=node.get('type','string')
    typelist=typ if isinstance(typ,list) else [typ]
    widget='checkbox-group' if 'array' in typelist and node.get('x-enum-group') else ('repeat-text' if 'array' in typelist else ('radio' if node.get('enum') else ('date' if node.get('format')=='date' else ('decimal-input' if node.get('pattern','').startswith('^(0|') else 'text'))))
    FIELDS.append({'fieldId': f'F{len(FIELDS)+1:03}', 'outputPath':path, 'categoryCondition':category, 'schemaPointer':spath, 'module':module, 'source':{'file':SOURCE.name, 'table':1, 'rowsOneBased':[r+1 for r in source_rows], 'labels':[ROW_LABELS[r] for r in source_rows], 'bodyParagraph': '具状人签字盖章及日期' if path.startswith('/签署信息/') else None}, 'description':node.get('description',''), 'schema':{k:v for k,v in node.items() if not k.startswith('x-')}, 'style':{'widget':widget}, 'missingValue':None, 'keyStatus':node.get('x-key-status','initial_mapping')})
walk(SCHEMA,'','#')
# Keep the original F001-F159 stable; append the new interest-rate supplement as F160.
RATE_SUPPLEMENT = SCHEMA['properties']['事实与理由']['properties']['5.借款利率']['properties']['补充内容']
FIELDS.append({
    'fieldId': f'F{len(FIELDS)+1:03}',
    'outputPath': '/事实与理由/5.借款利率/补充内容',
    'categoryCondition': None,
    'schemaPointer': '#/properties/事实与理由/properties/5.借款利率/properties/补充内容',
    'module': 'facts',
    'source': {'file': SOURCE.name, 'table': 1, 'rowsOneBased': [29], 'labels': [ROW_LABELS[28]], 'bodyParagraph': None},
    'description': RATE_SUPPLEMENT.get('description',''),
    'schema': {k:v for k,v in RATE_SUPPLEMENT.items() if not k.startswith('x-')},
    'style': {'widget': 'text'},
    'missingValue': None,
    'keyStatus': 'initial_mapping',
})
CONFIG = {
    'templateId':'private_lending_complaint', 'schemaVersion':'0.1.0', 'status':'initial_for_review',
    'sourceFile':SOURCE.name, 'schemaFile':'loan_complaint.schema.json',
    'contract':{'topLevelKeys':list(PROPS),'partyShape':'category + data','leafPropertiesAlwaysPresent':True,'missingValue':None,'emptyArraysAllowed':False,'money':'decimal string with exactly 2 digits after decimal point','date':'YYYY-MM-DD; incomplete dates retained in raw text or review evidence','phone':'string; retain multiple numbers and original separator','auditInBusinessResult':False},
    'sourceAuthority':{'formalLabels':'attachment','englishPartyKeys':'user example plus proposed mappings for missing fields','newSubfields':'engineering decomposition of source cells; initial mapping, not claimed to be existing source identifiers','numbering':'retain source 1-8, 1-2 and 1-18; trim layout spaces only'},
    'globalRules':[
        '仅抽取输入事实，模板说明、枚举展示及固定占位文字不是案件事实。',
        '已填文本与可靠勾选均可提供依据；专栏空白时可从案件正文提取明示值。',
        '多来源冲突不静默选值，相关字段返回null，候选值、来源和主体标识进入独立review。',
        '仅空白、全部未选、没有相关叙述时为null；未选不等于否、无或0。',
        '选项组不允许重复，不输出空数组或字符串null。',
        '不从身份号码推导出生日期或性别；不以当前日期填入落款、截止日或逾期时长。',
        '不把诉请利率标准当成合同约定利率，不把计息起算日当成借款到期日。',
        '其他项附加说明与该选项关联；选择其他但未提供说明时说明仍为null并按需复核。',
        '共享主体ID仅保存在内部索引和review，正式JSON不强行增加partyId。',
        'category表示数据结构类型，不表示原告被告等诉讼角色。'
    ],
    'enumGroups':GROUPS,
    'modules':[
        {'id':'parties','paths':['/原告','/被告','/第三人','/委托诉讼代理人']},
        {'id':'claims','paths':['/诉讼请求']},
        {'id':'facts','paths':['/事实与理由'],'optionalSplit':{'factsA':'原文及1至9项','factsB':'10至18项','overlapAllowed':False}},
        {'id':'remaining','paths':['/约定管辖和诉前保全','/对纠纷解决方式的意愿','/签署信息']}
    ],
    'runtime':{'workersDefault':5,'workersAllowed':[4,5],'globalModelQuotaRequired':True,'boundedSubmissionRequired':True,'queueCapacity':None,'requestTimeoutSeconds':None,'retryLimit':1,'quotaIncludes':['subject_index','extraction','retry']},
    'pendingDecisions':[
        '国有控股与参股是否允许同时选择；首版保留多选。',
        '提前还款与解除合同是否互斥；首版保留多选，不作为法律可并用结论。',
        '还款方式及登记类型是否需要按借款或担保物拆分；首版保留同组多选并提示互斥复核。',
        '多份保证合同、多担保物、不同代理委托关系是否需要升级为重复记录；首版不保证覆盖复杂多合同案件。',
        '用户示例ownership其他集体所有制是否接受规范为其他加ownershipOther集体所有制。',
        '本版phone保持字符串，多个号码后续是否升级为数组需接口版本变更。',
        '金额十进制字符串及新增细分子字段名称需要下游确认。',
        '本版不完整日期不补日，是否增加全局日期原文结构需确认。'
    ],
    'fields':FIELDS
}
write('loan_complaint.schema.json',SCHEMA)
write('field_config.json',CONFIG)

def blank(node):
    if '$ref' in node: return blank(DEFS[node['$ref'].split('/')[-1]])
    if 'properties' in node: return {k:blank(v) for k,v in node['properties'].items()}
    return None
sample=blank(SCHEMA)
sample['原告']=[{'category':'自然人','data':{**blank(natural),'name':'金洲','sex':'男','birthDate':'1995-09-18','ethnicity':'汉族','phone':'15005798171','residence':'浙江省义乌市大陈镇红峰村金钩10号','certificateType':'中华人民共和国居民身份证','certificateNumber':'330782199509183111'}}]
sample['被告']=[{'category':'自然人','data':{**blank(natural),'name':'赵月','sex':'女','birthDate':'1989-07-31','ethnicity':'汉族','phone':'18686785309、18858183390','residence':'哈尔滨市道里区群力第一大道1773号群力新城北区3栋1单元702室','habitualResidence':'杭州市萧山区滨涛映月4幢1501','certificateType':'中华人民共和国居民身份证','certificateNumber':'230102198907310742'}}]
def cell_text(row):
    r=table.findall('w:tr',NS)[row]
    tc=r.findall('w:tc',NS)[-1]
    lines=[]
    for p in tc.findall('w:p',NS):
        parts=[]
        for el in p.iter():
            t=etree.QName(el).localname
            if t=='t': parts.append(el.text or '')
            elif t=='br': parts.append('\n')
        lines.append(''.join(parts))
    return '\n'.join(lines).strip()
c=sample['诉讼请求']
c['诉讼请求原文']=cell_text(10)
c['1.本金'].update({'截至日期':'2026-06-16','尚欠本金':'50000.00','币种':'人民币'})
calc='以人民币50000元为基数，按照全国银行间同业拆借中心公布的一年期贷款市场报价利率计算，自2026年4月1日起计至实际偿还完毕之日止，暂计至2026年6月16日'
c['2.利息'].update({'截至日期':'2026-06-16','尚欠利息':'320.83','计算方式':calc,'计算基数':'50000.00','利率标准':'全国银行间同业拆借中心公布的一年期贷款市场报价利率','起算日期':'2026-04-01','暂计截止日期':'2026-06-16','是否请求支付至实际清偿之日止':'是'})
c['5.是否主张实现债权的费用'].update({'是否主张':'是','明细':'诉讼费用（保全费用，如有）'})
c['6.是否主张诉讼费用']='是'
c['8.标的总额'].update({'金额':'50320.83','暂计截止日期':'2026-06-16'})
f=sample['事实与理由']
f['事实与理由原文']=cell_text(23)
f['1.合同的签订情况（名称、编号、签订时间、地点等）']['签订情况原文']='无书面合同'
f['2.签订主体'].update({'出借人':['金洲'],'借款人':['赵月']})
f['3.借款金额'].update({'实际提供':'50000.00','提供方式':['转账']})
f['4.借款期限'].update({'是否到期':'是','期限原文':'承诺于2026年3月底前归还'})
f['6.借款提供时间']=[{'提供日期':'2025-12-07','金额':'50000.00'}]
f['9.是否存在逾期还款']['是否存在']='是'
for key,sel in [('10.是否签订物的担保（抵押、质押）合同','是否签订'),('12.是否最高额担保（抵押、质押）','是否最高额担保'),('13.是否办理抵押、质押登记','是否办理'),('14.是否签订保证合同','是否签订'),('15.其他担保方式','是否存在')]: f[key][sel]='否'
f['17.请求依据']['法律规定']='《中华人民共和国民法典》第六百七十五条、第六百七十六条、第六百七十九条'
m=sample['对纠纷解决方式的意愿']
m[next(iter(m))]='了解'
m['是否了解先行调解解决纠纷的好处']={k:'了解' for k,_ in benefits}
m['是否考虑先行调解']='是'
write('example_from_attachment.json',sample)
org_fragment={'原告':[{'category':'法人、非法人组织','data':{**blank(organization),'name':'宜昌示例工程有限公司','registrationPlace':'宜昌市伍家岗区示例大道30号','legalRep':'周总','position':'董事长','phone':'07170000001','creditCode':'91420500EXAMPLE007','type':['有限责任公司'],'ownership':'其他','ownershipOther':'集体所有制'}}]}
write('organization_fragment.json',org_fragment)

review={
    'schemaVersion':'0.1.0','businessFile':'example_from_attachment.json','status':'needs_confirmation',
    'scope':'人工按附件文本和勾选状态整理的参考样例；不是运行模型后的准确率评估，也不验证法律适用或利息计算。',
    'internalSubjectIndex':[{'subjectId':'P1','role':'原告','name':'金洲','outputPointer':'/原告/0'},{'subjectId':'P2','role':'被告','name':'赵月','outputPointer':'/被告/0'}],
    'evidence':[
        {'paths':['/原告/0/data/sex'],'sourceRowOneBased':3,'quote':'男þ 女¨','kind':'checked'},
        {'paths':['/被告/0/data/sex'],'sourceRowOneBased':6,'quote':'男¨ 女þ','kind':'checked'},
        {'paths':['/事实与理由/3.借款金额/实际提供','/事实与理由/3.借款金额/提供方式','/事实与理由/6.借款提供时间/0/提供日期'],'sourceRowOneBased':24,'quote':'2025年12月7日……原告出于信任，于当日通过工商银行向被告转账50000元','kind':'explicit_narrative'},
        {'paths':['/事实与理由/4.借款期限/是否到期'],'sourceRowOneBased':24,'quote':'现还款期限已过','kind':'explicit_narrative'},
        {'paths':['/事实与理由/10.是否签订物的担保（抵押、质押）合同/是否签订','/事实与理由/12.是否最高额担保（抵押、质押）/是否最高额担保','/事实与理由/13.是否办理抵押、质押登记/是否办理','/事实与理由/14.是否签订保证合同/是否签订','/事实与理由/15.其他担保方式/是否存在'],'sourceRowsOneBased':[34,36,37,38,39],'quote':'对应栏位否þ','kind':'checked'},
        {'paths':['/对纠纷解决方式的意愿'],'sourceRowsOneBased':[44,45,46],'quote':'了解项及先行调解是项的Wingdings字符00FE；不是空框00A8','kind':'checked_symbol'}
    ],
    'issues':[
        {'type':'unselected','paths':['/委托诉讼代理人/是否委托'],'message':'有、无均未勾选且未填代理人，保持null，不能填无。'},
        {'type':'unselected','paths':['/诉讼请求/3.是否要求提前还款或解除合同/是否要求','/诉讼请求/4.是否主张担保权利/是否主张','/约定管辖和诉前保全'],'message':'相关选项均空白，不能由其他否定信息级联推定。'},
        {'type':'partial_date','paths':['/事实与理由/4.借款期限/约定截止日期'],'quote':'2026年3月底前','message':'本版保留期限原文，不补成3月31日；如业务允许语义归一化需另定规则。'},
        {'type':'distinct_semantics','paths':['/事实与理由/5.借款利率','/诉讼请求/2.利息/利率标准'],'message':'诉请采用LPR不等于双方约定借款利率；合同利率字段保持null。'},
        {'type':'conditional_cost','paths':['/诉讼请求/5.是否主张实现债权的费用/明细'],'quote':'诉讼费用（保全费用，如有）','message':'保留如有，不能据此确定已经保全。'},
        {'type':'not_a_value','paths':['/事实与理由/9.是否存在逾期还款/逾期时长原文'],'message':'至今已逾期是未填完的表格提示，不作为逾期时长。'},
        {'type':'not_an_exhibit_list','paths':['/事实与理由/18.证据清单（可另附页）'],'message':'事实提及通话录音，但正式证据清单空白，首版不生成证据编号或清单。'},
        {'type':'blank_signature','paths':['/签署信息'],'message':'落款与日期未填写，不从原告姓名或系统日期补齐。'},
        {'type':'not_recalculated','paths':['/诉讼请求/2.利息/尚欠利息'],'message':'320.83仅按原文提取，未校验LPR数值、日计数或计息结果。'}
    ],
    'upstreamRequirement':{'checkboxEvidenceRequired':True,'supportedExamples':['☑','☒','□','þ','¨','w:sym Wingdings 00FE/00A8'],'policy':'必须保留选项文字、勾选状态和字符字体来源；如解析后只剩选项名而无状态，返回null并提示信息丢失，不猜测。','fontCaution':'þ和¨只在已确认的来源字体映射下转换，不作任意文本的全局替换。'}
}
write('example_review.json',review)

metrics={'sourceTableRows':len(ROWS),'fieldConfigurations':len(FIELDS),'fieldCountDefinition':'按最终角色路径及category分支展开后的可填值；不含固定category、结构容器和运行参数。','fieldConfigurationsByModule':dict(Counter(x['module'] for x in FIELDS)),'enumGroups':len(GROUPS),'organizationTypeOptions':len(GROUPS['orgType']['values']),'mediationChoiceGroups':7,'topLevelKeys':list(PROPS)}
write('inventory.json',metrics)
dictionary=['# 民间借贷起诉状初版字段字典','',
    f'共{len(FIELDS)}条配置，按角色和category分支展开。所有金额采用元和两位小数的字符串；未知值为null。固定容器和category不计入此数。字段中英文键与细分子项为初版映射，原文编号保留。',
    '', '完整约束以loan_complaint.schema.json为准；本表用于人工审阅。','']
for module in ['parties','claims','facts','remaining']:
    dictionary += ['## '+{'parties':'当事人与代理人','claims':'诉讼请求','facts':'事实与理由','remaining':'管辖保全调解及签署'}[module],'',
        '| 编号 | 输出路径 | 主体类别 | 类型及选项 | 来源表格行 | 抽取说明 |','| --- | --- | --- | --- | --- | --- |']
    for f in [x for x in FIELDS if x['module']==module]:
        s=f['schema']; t=s.get('type','string'); t='/'.join(t) if isinstance(t,list) else t
        vals=s.get('enum') or s.get('items',{}).get('enum')
        if vals:t+='；'+ '、'.join(str(v) for v in vals if v is not None)
        if s.get('format'):t+='；'+s['format']
        if s.get('pattern'):t+='；有格式约束'
        clean=lambda v:str(v).replace('|','\\|').replace('\n',' ')
        row=[f['fieldId'],f['outputPath'],f['categoryCondition'] or '—',t,','.join(map(str,f['source']['rowsOneBased'])) or '正文落款',f['description']]
        dictionary.append('| '+' | '.join(clean(v) for v in row)+' |')
    dictionary.append('')
(OUT/'field_dictionary.md').write_text('\n'.join(dictionary)+'\n',encoding='utf-8')
print(json.dumps(metrics,ensure_ascii=False))
