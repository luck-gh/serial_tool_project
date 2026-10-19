#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""串口调试线程:后台打开和读取串口,调用线程写入,使用 Qt 信号回传数据和错误.

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import threading

import serial
import serial.tools.list_ports
from PyQt5.QtCore import QThread, pyqtSignal

class SerialThread(QThread):
    """普通调试串口的句柄拥有者,负责打开,接收和最终关闭.

    run 在工作线程读取,write_data/set_baudrate 在调用方线程执行;上层须协调
    发送,参数修改与固件独占.stop 请求取消读取并等待退出,最终由 run 关闭
    句柄.open_completed 只表示打开尝试已结束,调用方还须检查 open_error
    和实际连接状态.data_received 携带原始字节,error_occurred 携带说明.
    """
    data_received = pyqtSignal(bytes)
    error_occurred = pyqtSignal(str)

    def __init__(self, port, baudrate, bytesize, parity, stopbits):
        """保存串口参数,创建停止和打开完成事件;构造阶段不访问设备.

        Args:
            port (str): 设备端口名,例如 COM3,不包含 UI 的端口描述.
            baudrate (int): 设备使用的波特率.
            bytesize (int): pySerial 数据位常量,通常为 5,6,7 或 8.
            parity (str): pySerial 校验常量,例如 N,E,O 或 M.
            stopbits (float): pySerial 停止位常量,1,1.5 或 2.
        """
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.serial = None
        self.is_running = False
        self._stop_requested = threading.Event()
        self.open_completed = threading.Event()
        self.open_error = ""

    def run(self):
        """打开设备并通知打开结果,阻塞读取首字节后合并当前缓冲数据.

        成功打开后先清空输入缓冲.读取超时为 100 ms,写入超时为 1 s;正常取消
        引起的读取异常不作为故障上报.任何退出路径都置位 open_completed,
        并由本线程关闭句柄,避免 stop 与 finally 并发释放 Windows 句柄.
        """
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=self.bytesize,
                parity=self.parity,
                stopbits=self.stopbits,
                timeout=0.1,
                write_timeout=1.0
            )
            if self._stop_requested.is_set():
                return
            self.is_running = True
            
            # 打开串口后立即清空输入缓冲区,防止波特率切换产生的垃圾数据导致解码错误
            if self.serial.is_open:
                self.serial.reset_input_buffer()
            self.open_completed.set()

            while (
                self.is_running
                and not self._stop_requested.is_set()
                and self.serial
                and self.serial.is_open
            ):
                try:
                    # 阻塞等待首字节,避免在无数据时反复查询 in_waiting
                    # 造成线程空转.首字节到达后,再一次性取出当前缓冲区
                    # 中已有的数据,以减少信号发送和 UI 刷新次数.
                    data = self.serial.read(1)
                    if not data:
                        continue

                    waiting = self.serial.in_waiting
                    if waiting:
                        data += self.serial.read(waiting)
                    self.data_received.emit(data)
                except Exception as e:
                    # stop() 先将 is_running 置为 False,再尝试取消阻塞读取;
                    # 正常退出期间产生的异常不应显示为读取错误,句柄由 run() 关闭.
                    if self.is_running and not self._stop_requested.is_set():
                        self.error_occurred.emit(f"读取错误: {str(e)}")
                    break

        except Exception as e:
            self.open_error = str(e)
            if not self._stop_requested.is_set():
                self.error_occurred.emit(f"串口被占用: {str(e)}")
        finally:
            self.open_completed.set()
            self.is_running = False
            serial_port = self.serial
            if serial_port and serial_port.is_open:
                try:
                    # 串口句柄只由工作线程关闭,避免 Windows pySerial
                    # 在 stop() 与 finally 中并发 close() 导致句柄竞态.
                    serial_port.close()
                except Exception:
                    pass
            self.serial = None

    def write_data(self, data):
        """尝试写入一次数据;短写或异常不会自动重发.

        Args:
            data (bytes): 要写出的原始串口字节.

        Returns:
            int: 完整写入时返回字节数;未打开,短写或异常返回 0.0 不能证明设备
                没有收到任何字节,完整写入也不能证明设备已执行命令.
        """
        if self.serial and self.serial.is_open:
            try:
                count = self.serial.write(data)
                if count != len(data):
                    self.error_occurred.emit(f"串口短写: {count}/{len(data)}；结果未知，未重发")
                    return 0
                return count
            except Exception as e:
                self.error_occurred.emit(f"发送错误: {str(e)}")
                return 0
        return 0

    def set_baudrate(self, baudrate):
        """更新已打开句柄的波特率并清空输入缓冲,返回是否完成.

        Args:
            baudrate (int): 设备支持的目标波特率.

        Returns:
            bool: 设置并清空缓冲均成功时为 True;异常或句柄未打开时为 False.
        """
        if self.serial and self.serial.is_open:
            try:
                self.serial.baudrate = baudrate
                self.baudrate = baudrate
                # 清除输入缓冲区以避免波特率不匹配产生的垃圾数据
                self.serial.reset_input_buffer()
                return True
            except Exception as e:
                self.error_occurred.emit(f"设置波特率失败: {str(e)}")
                return False
        return False

    def stop(self):
        """设置停止事件,尝试 cancel_read 并等待工作线程退出.

        不在调用线程中关闭句柄;读取可能已自然结束,此时忽略取消读取的异常.

        Returns:
            bool: 线程在 1500 ms 内退出为 True;False 时调用方须保留线程对象.
        """
        self._stop_requested.set()
        self.is_running = False

        # 只取消阻塞读取,不在调用线程中关闭串口.run() 的 finally
        # 会统一关闭句柄,保证 Windows 重叠 I/O 句柄只释放一次.
        serial_port = self.serial
        if serial_port and serial_port.is_open:
            cancel_read = getattr(serial_port, "cancel_read", None)
            if callable(cancel_read):
                try:
                    cancel_read()
                except Exception:
                    # 读取可能已自然结束,或工作线程已经开始清理.
                    pass
        return self.wait(1500)
