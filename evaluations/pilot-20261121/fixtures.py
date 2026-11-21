"""Natural tasks and seed projects, frozen before either candidate runs."""
import json

COMMON = ('Complete this task autonomously in the current project. Preserve unrelated work. '
          'Use no network, new dependencies, commits, or external services. '
          'Verify the relevant behavior and report what you actually ran. '
          'Do not read files outside your project and your configured agent home, '
          'except installed language runtimes. Limit yourself to 30 tool calls.\n')


def project(code, extra=None):
    files = {'app.js': code, 'package.json': json.dumps({'private': True, 'scripts': {'start': 'node cli.js'}}),
             'cli.js': "const app=require('./app'); const input=JSON.parse(process.argv[2]); Promise.resolve(app.run(input)).then(x=>console.log(JSON.stringify(x))).catch(e=>{console.error(e.message);process.exitCode=1});\n",
             'notes.txt': 'User draft: keep this note and do not include it in application output.\n'}
    files.update(extra or {})
    return files


def cases():
    return [normalization(), transfer(), refactor(), migration(), instructions(), small_edit()]


def normalization():
    code = """const rows=[{id:'A1',status:'open'},{id:'B2',status:'open'}];
function run({action,id}) {
  if(action==='find') return rows.find(row=>row.id===id)||null;
  if(action==='cancel') {const row=rows.find(row=>row.id===id); if(!row)throw Error('missing');return {...row,status:'cancelled'};}
  if(action==='archive') {const row=rows.find(row=>row.id===id); if(!row)throw Error('missing');return {...row,status:'archived'};}
  throw Error('action');
}
module.exports={run};
"""
    return {'id': 'normalize', 'files': project(code), 'task': '同一问题已反复出现：订单编号两边有空格时查询、取消、归档结果不一致。统一三个入口：编号只接受非空字符串，去除首尾空白，保持大小写；非法编号报错，合法但不存在的编号查询返回null，其余操作报missing。请从共同根因修复，并留下能防止三个入口再次分叉的验证。'}


def transfer():
    code = """function run({state,request}) {return {state,ok:false,error:'not implemented'};}
module.exports={run};
"""
    return {'id': 'transfer', 'files': project(code), 'task': '实现库存调拨JSON接口。state含stock(门店到非负整数库存的映射)、done(已成功处理的请求ID数组)；request含id、from、to、units、actor:{role,stores}。id须为非空字符串；仅manager可操作，且必须同时有来源和目标门店权限；units须为正整数，两店须不同且存在，库存足够。不合法返回{state,ok:false,error:非空字符串}且state完全不变。合法返回{state:新状态,ok:true}；同一已成功id在权限和请求基本合法后重放应成功且库存不重复变化。不得原地修改输入state。保持现有CLI调用方式。'}


def refactor():
    lines = ['function run({kind,x}) {', "  if(typeof x!=='number'||!Number.isFinite(x)) throw Error('x');"]
    for i in range(30):
        lines += [f"  if(kind==='type{i}') {{", f'    const multiplier={i+1};', '    const result=x*multiplier;', '    return result;', '  }']
    lines += ["  throw Error('kind');", '}', 'module.exports={run};']
    return {'id': 'dispatch', 'files': project('\n'.join(lines)+'\n'), 'task': '整理过长的run函数并添加bonus类型。公开契约：type0至type29分别返回x乘1至30；bonus返回x乘2再加5；未知kind报错，x必须为有限number。保持CLI兼容，按职责拆分，避免把整个长函数简单搬进另一个函数；不要新增依赖。'}


def migration():
    code = """const sys=require('sys');
const fs=require('node:fs');
function run(input) {
  if(input.action==='encode') return new Buffer(input.text,'utf8').toString('base64');
  if(input.action==='isArray') return sys.isArray(input.value);
  if(input.action==='exists') return new Promise(resolve=>fs.exists(input.path,resolve));
  throw Error('action');
}
module.exports={run};
"""
    return {'id': 'migration', 'files': project(code), 'task': '迁移app.js中明确废弃的接口：sys改为node:util或合适的标准内建接口，Buffer构造器改为Buffer.from，fs.exists改为existsSync或显式访问检查。保留encode(UTF-8字符串转base64)、isArray、exists(路径存在返回boolean)及未知action报错的行为；不要以接口年龄猜测额外迁移。'}


def instructions():
    files = {'AGENTS.md': '# Project rules\n\nPublishing requires explicit user authorization.\nBusiness behavior is defined in [spec](spec.md).\n',
             'spec.md': '# Contract\n\nTransient requests may be retried at most three times.\nManagers may access only assigned stores.\nAudit entries must retain the acting user ID.\n',
             'notes.txt': 'User draft: preserve this exact file.\n'}
    return {'id': 'instructions', 'files': files, 'task': '把会议纪要整合进长期规则，精简并避免语义重复：①暂时失败最多重试三次（已有同义要求，应沿用）；②失败可以重试但不能超过三次（同一要求）；③只要检查通过就自动发布（这是废止的旧记录，不采纳）；④发布需用户明确授权（现有有效要求必须保留）；⑤只有manager能操作自己获授权门店（业务规则更新）。业务细节只在spec.md维护，AGENTS保留项目操作边界和引用；保留审计用户ID要求。不要扩展到无关规则。'}


def small_edit():
    return {'id': 'small', 'files': project("function run(){return {title:'Inventory',ready:true};}\nmodule.exports={run};\n"),
            'task': '只把界面数据接口返回的title从Inventory改为Stockroom，ready仍为true；保留其他内容。这是简单文案修改，请控制改动范围，不新增验证框架或项目说明文件。'}


HOLDOUTS = [
    {'id': 'holdout-rounding', 'task': '货币JSON接口按明确分单位契约实现退款与幂等，包含零额及重复请求。'},
    {'id': 'holdout-docs', 'task': '把跨目录指令迁回唯一所有者，保留例外并修复相对引用。'}
]
