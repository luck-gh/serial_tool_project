#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
串口线程模块, 负责在后台线程中执行串口打开, 读取, 写入和错误上报。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import threading

import serial
import serial.tools.list_ports
from PyQt5.QtCore import QThread, pyqtSignal

class SerialThread(QThread):
    """串口通信线程"""
    data_received = pyqtSignal(bytes)
    error_occurred = pyqtSignal(str)

    def __init__(self, port, baudrate, bytesize, parity, stopbits):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.serial = None
        self.is_running = False
        self._stop_requested = threading.Event()

    def run(self):
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=self.bytesize,
                parity=self.parity,
                stopbits=self.stopbits,
                timeout=0.1
            )
            if self._stop_requested.is_set():
                return
            self.is_running = True
            
            # 打开串口后立即清空输入缓冲区，防止波特率切换产生的垃圾数据导致解码错误
            if self.serial.is_open:
                self.serial.reset_input_buffer()

            while (
                self.is_running
                and not self._stop_requested.is_set()
                and self.serial
                and self.serial.is_open
            ):
                try:
                    # 阻塞等待首字节，避免在无数据时反复查询 in_waiting
                    # 造成线程空转。首字节到达后，再一次性取出当前缓冲区
                    # 中已有的数据，以减少信号发送和 UI 刷新次数。
                    data = self.serial.read(1)
                    if not data:
                        continue

                    waiting = self.serial.in_waiting
                    if waiting:
                        data += self.serial.read(waiting)
                    self.data_received.emit(data)
                except Exception as e:
                    # stop() 会先将 is_running 置为 False，再关闭串口以解除
                    # 阻塞读取；这种正常退出产生的异常不应显示为读取错误。
                    if self.is_running and not self._stop_requested.is_set():
                        self.error_occurred.emit(f"读取错误: {str(e)}")
                    break

        except Exception as e:
            if not self._stop_requested.is_set():
                self.error_occurred.emit(f"串口被占用: {str(e)}")
        finally:
            self.is_running = False
            serial_port = self.serial
            if serial_port and serial_port.is_open:
                try:
                    # 串口句柄只由工作线程关闭，避免 Windows pySerial
                    # 在 stop() 与 finally 中并发 close() 导致句柄竞态。
                    serial_port.close()
                except Exception:
                    pass
            self.serial = None

    def write_data(self, data):
        """发送数据"""
        if self.serial and self.serial.is_open:
            try:
                self.serial.write(data)
                return len(data)
            except Exception as e:
                self.error_occurred.emit(f"发送错误: {str(e)}")
                return 0
        return 0

    def set_baudrate(self, baudrate):
        """动态设置波特率"""
        self.baudrate = baudrate
        if self.serial and self.serial.is_open:
            try:
                self.serial.baudrate = baudrate
                # 清除输入缓冲区以避免波特率不匹配产生的垃圾数据
                self.serial.reset_input_buffer()
                return True
            except Exception as e:
                self.error_occurred.emit(f"设置波特率失败: {str(e)}")
                return False
        return False

    def stop(self):
        """停止线程"""
        self._stop_requested.set()
        self.is_running = False

        # 只取消阻塞读取，不在调用线程中关闭串口。run() 的 finally
        # 会统一关闭句柄，保证 Windows overlapped handle 只释放一次。
        serial_port = self.serial
        if serial_port and serial_port.is_open:
            cancel_read = getattr(serial_port, "cancel_read", None)
            if callable(cancel_read):
                try:
                    cancel_read()
                except Exception:
                    # 读取可能已自然结束，或工作线程已经开始清理。
                    pass
        self.wait(1000)
