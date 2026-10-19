"""命令类型与输出来源;供 CLI 和 GUI 共用,不依赖界面库."""
from enum import Enum


class OutputSource(Enum):
    """输出来源类型"""
    SEND = "send"      # 发送数据
    RECEIVE = "receive" # 接收数据
    SYSTEM = "system"  # 系统信息
    ERROR = "error"    # 错误信息


class SpecialCommandType(Enum):
    """特殊指令类型"""
    MODE = "mode"           # 模块命名
    MODEEND = "modeend"     # 结束模块定义
    DELAY = "delay"         # 延迟设置
    SENDHEX = "sendhex"     # 十六进制发送
    BAUDRATE = "baudrate"   # 波特率设置
    COMPORT = "comport"     # COM口设置
    SETENDLOG = "setendlog" # 结尾符设置
    SENDMODE = "sendmode"   # 发送指定模块
    STOPCONTINUOUS = "stopcontinuous"  # 停止连续发送(可选择是否停止循环)
    FIRMWAREDOWNLOAD = "firmwaredownload"  # 固件下载
