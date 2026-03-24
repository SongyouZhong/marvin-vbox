#!/bin/bash

# 检查是否输入了化学式参数
if [ -z "$1" ]; then
  echo "用法: ./run_cxcalc.sh \"你的化学式\""
  exit 1
fi

SMILES=$1

echo "正在将 $SMILES 发送到 Windows 虚拟机进行计算..."

# 1. 自动组装命令并进行 Base64 编码
RAW_CMD=".\cxcalc.bat -S \"$SMILES\" logP logD pKa -a 2 -b 2"
CMD_B64=$(echo -n "$RAW_CMD" | iconv -t UTF-16LE | base64 -w 0)

# 2. 发送给虚拟机执行
vboxmanage guestcontrol "Win11VM" run \
  --exe 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' \
  --username "marvin-box" \
  --password "123123" \
  --wait-stdout \
  --cwd 'C:\Program Files (x86)\ChemAxon\MarvinBeans\bin' \
  -- powershell.exe -NonInteractive -EncodedCommand $CMD_B64