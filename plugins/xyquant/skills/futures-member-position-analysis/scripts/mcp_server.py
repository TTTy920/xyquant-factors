"""stdio MCP 服务。服务端调用受限子进程；不向调用方暴露凭据、环境或文件读取能力。"""
import base64,json,os,subprocess,sys,threading,uuid
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent,ImageContent

ROOT=Path(__file__).resolve().parent
mcp=FastMCP('futures-member-position-analysis')
LOCK=threading.Lock()

def calculate(symbol,mode,date,windows=(60,120,250),lookback=20,top_n=5,scope='dominant',contract=None):
    # 这里只能执行固定计算脚本，不执行用户代码、shell 或用户指定的文件路径。
    out=Path(os.environ.get('FUTURES_OUTPUT_DIR','./futures-output')).resolve()/uuid.uuid4().hex
    out.mkdir(parents=True,exist_ok=True)
    engine=os.environ.get('FUTURES_ENGINE_PYTHON',sys.executable)
    cmd=[engine,str(ROOT/'member_live.py'),symbol,'--mode',mode,'--windows',','.join(str(x) for x in windows),'--lookback',str(lookback),'--top-n',str(top_n),'--scope',scope,'--output-dir',str(out)]
    if date:cmd+=['--date',date]
    if contract:cmd+=['--contract',contract]
    with LOCK:
        try:proc=subprocess.run(cmd,capture_output=True,text=True,timeout=1200)
        except Exception:return [TextContent(type='text',text='后端暂不可用，请管理员检查计算环境；未返回内部错误详情')]
    try:result=json.loads(proc.stdout)
    except Exception:return [TextContent(type='text',text='后端未返回有效计算结果，请联系管理员；未返回内部日志')]
    image=None
    if result.get('ok') and 'heatmap' in result:
        file=Path(result['heatmap'].pop('image_path')).resolve()
        if file.is_relative_to(out) and file.exists():image=file.read_bytes()
    content=[TextContent(type='text',text=json.dumps(result,ensure_ascii=False))]
    if image:content.append(ImageContent(type='image',data=base64.b64encode(image).decode(),mimeType='image/png'))
    return content

@mcp.tool()
def compute_futures_crowding(symbol:str,date:str|None=None,windows:list[int]|None=None):
    """计算指定期货品种的原始拥挤度指标1及历史分位数。支持代码或豆粕等中文名；默认最新完整交易日，窗口60/120/250交易日。分位数含当日，不做信号平滑或取反。"""
    return calculate(symbol,'crowding',date,windows if windows is not None else (60,120,250))

@mcp.tool()
def get_futures_member_heatmap(symbol:str,date:str|None=None,lookback:int=20,top_n:int=5,scope:str='dominant',contract:str|None=None):
    """生成指定品种最新会员净持仓相关性热力图并直接返回图片。默认20交易日、两端各5家；商品默认目标日主力单合约，金融默认全合约。scope=strategy时商品用每日主力+次主力。可明确指定单合约。"""
    return calculate(symbol,'heatmap',date,lookback=lookback,top_n=top_n,scope=scope,contract=contract)

if __name__=='__main__':mcp.run(transport='stdio')
