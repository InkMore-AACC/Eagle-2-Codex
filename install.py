"""Portable installer; no third-party Python packages."""
import argparse, json, shutil, subprocess, sys, time
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');a=p.parse_args()
 root=Path(__file__).resolve().parent
 dest=Path.home()/'plugins/library-hub'
 market=Path.home()/'.agents/plugins/marketplace.json'
 cli=shutil.which('codex')
 if sys.version_info<(3,10):raise RuntimeError('需要 Python 3.10 或更新版本。')
 if not cli:raise RuntimeError('没有找到 Codex 命令。请先安装 Codex CLI，并重新运行安装程序。')
 for f in ['plugin/.codex-plugin/plugin.json','plugin/scripts/hub.py','helpers/create_basic_plugin.py']:
  if not (root/f).is_file():raise RuntimeError('请完整解压安装包后运行：'+f)
 print('插件安装位置：'+str(dest))
 print('Python：'+sys.executable)
 if a.check:
  print('安装前检查通过，未修改任何文件。');return
 stamp=time.strftime('%Y%m%d-%H%M%S')
 if dest.exists():
  backup=dest.with_name('library-hub-backup-'+stamp)
  shutil.copytree(dest,backup)
  print('旧插件备份：'+str(backup))
 if market.exists():shutil.copy2(market,market.with_name('marketplace-backup-'+stamp+'.json'))
 existing=json.loads(market.read_text(encoding='utf-8')) if market.exists() else {}
 entry=next((x for x in existing.get('plugins',[]) if x.get('name')=='library-hub'),None)
 if entry:
  if entry.get('source')!={'source':'local','path':'./plugins/library-hub'}:
   raise RuntimeError('已有同名插件指向其他来源，保留原设置并停止安装。')
 else:
  subprocess.run([sys.executable,str(root/'helpers/create_basic_plugin.py'),'library-hub','--with-marketplace','--force'],check=True)
 shutil.copytree(root/'plugin',dest,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
 mcp={'mcpServers':{'library':{'command':sys.executable,'args':['-X','utf8',str(dest/'scripts/hub.py'),'mcp']}}}
 (dest/'.mcp.json').write_text(json.dumps(mcp,ensure_ascii=False,indent=2),encoding='utf-8')
 subprocess.run([sys.executable,str(root/'helpers/update_plugin_cachebuster.py'),str(dest)],check=True)
 name=subprocess.check_output([sys.executable,str(root/'helpers/read_marketplace_name.py'),'--marketplace-path',str(market)],text=True).strip()
 # Registry names are validated by the bundled scaffold helper.
 subprocess.run(['powershell','-NoProfile','-Command',"& codex plugin add 'library-hub@"+name+"' --json"],check=True)
 print('安装成功。打开 Eagle，再在 Codex 新建对话说“打开库”。无需新的 Codex 启动器。')
 print('本安装程序不会自动迁移或覆盖 Eagle 图片；恢复素材请查看使用说明。')

if __name__=='__main__':
 try:main()
 except Exception as e:print('安装未完成：'+str(e));sys.exit(1)
