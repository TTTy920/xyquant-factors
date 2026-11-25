"""Receive a ready MCP dataset job through private stdin or one-shot loopback."""
import argparse
import json
from pathlib import Path
import sys
from zipfile import BadZipFile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import xyquant_dataset_download as datasets
from mcp_receive import MAX_JOB_BYTES, receive_job


def accept(project, job_id, raw):
    try:
        if len(raw)>MAX_JOB_BYTES:raise ValueError('下载任务描述过大')
        job=json.loads(raw)
        if not isinstance(job,dict) or job.get('id')!=job_id:raise ValueError('下载任务编号不一致')
        return datasets.save(project,job)
    except (OSError,ValueError,KeyError,TypeError,BadZipFile,datasets.DownloadError):
        return {'status':'failed','message':'基础数据下载或校验失败；请检查空间、数据版本及下载凭据后重试'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('status','save','receive'))
    parser.add_argument('--project',required=True,type=Path)
    parser.add_argument('--job')
    parser.add_argument('--dataset',action='append',choices=sorted(datasets.DATASETS))
    parser.add_argument('--wait-seconds',type=int,default=120,choices=range(15,241),metavar='15..240')
    args=parser.parse_args()
    if not args.project.is_absolute() or not args.project.is_dir():parser.error('existing absolute project required')
    if args.action=='status':
        if not args.dataset:parser.error('--dataset required')
        result=datasets.status(args.project,args.dataset)
    else:
        if not args.job or not datasets.re.fullmatch(r'[a-f0-9]{32}',args.job):parser.error('valid --job required')
        result=(accept(args.project,args.job,sys.stdin.buffer.read(MAX_JOB_BYTES+1)) if args.action=='save' else
                receive_job(args.job,args.wait_seconds,lambda raw:accept(args.project,args.job,raw)))
    print(json.dumps(result,ensure_ascii=True))
    return 0 if result['status']=='ready' else 2


if __name__=='__main__':
    raise SystemExit(main())
