# Core 模块

`core` 模块包含应用程序的核心业务逻辑。

## `serial_thread.py`

- **`SerialThread`**: 继承自 `QThread`，负责在后台线程中处理所有串口通信，包括数据的读取和写入。这可以防止在进行耗时的I/O操作时阻塞GUI主线程。
  - `data_received`: 当接收到新数据时发出的信号。
  - `error_occurred`: 当发生串口错误时发出的信号。
