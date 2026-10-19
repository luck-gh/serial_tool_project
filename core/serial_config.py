"""本机与远程共用的串口参数校验与 pySerial 参数转换,不访问设备或界面."""
import serial


def serial_kwargs(config):
    """将应用串口设置转换为 pySerial/下载器共用的五个连接参数.

    Args:
        config (dict): port 可包含显示描述;baudrate/databits/parity/stopbits
            使用应用配置格式,缺省为 115200,8,None,1.

    Returns:
        dict: port,baudrate,bytesize,parity,stopbits,不包含读写超时.
            不访问设备,完整请求的合法性仍由入口校验.
    """
    return dict(
        port=(config.get("port") or "").strip().split(" ")[0],
        baudrate=int(config.get("baudrate", "115200")),
        bytesize=int(config.get("databits", "8")),
        parity={"None": serial.PARITY_NONE, "Even": serial.PARITY_EVEN,
                "Odd": serial.PARITY_ODD, "Mark": serial.PARITY_MARK}.get(
                    config.get("parity", "None"), serial.PARITY_NONE),
        stopbits={"1": serial.STOPBITS_ONE, "1.5": serial.STOPBITS_ONE_POINT_FIVE,
                  "2": serial.STOPBITS_TWO}.get(str(config.get("stopbits", "1")), serial.STOPBITS_ONE),
    )


def validate_baudrate(value):
    """将整数形式的波特率转为 int,拒绝布尔值及 1-4000000 之外的值.

    Args:
        value (int | str): 待校验的波特率,不接受小数表示.

    Returns:
        int: 校验后的波特率;数值合法不保证设备支持.

    Raises:
        ValueError: 参数不是合法的波特率整数.
    """
    if isinstance(value, bool) or not str(value).isdigit() or not 0 < int(value) <= 4000000:
        raise ValueError("波特率必须为 1–4000000 的整数")
    return int(value)


def validate_serial_config(config):
    """在修改 UI 前校验完整串口配置,避免不可编辑下拉框静默忽略非法值.

    Args:
        config (dict): port 为非空端口名;baudrate 为合法整数;databits 为
            5/6/7/8;parity 为 None/Even/Odd/Mark;stopbits 为 1/1.5/2.
            枚举字段可用对应字符串,端口名最多 256 字符且不含换行或空字符.

    Returns:
        dict: 原配置对象,不替换字段,不打开串口.

    Raises:
        ValueError: 配置类型,字段值或范围不符合契约.
    """
    if not isinstance(config, dict):
        raise ValueError("串口参数必须为对象")
    port = config.get("port")
    if not isinstance(port, str) or not port.strip() or len(port) > 256 or any(c in port for c in "\r\n\0"):
        raise ValueError("串口名称无效")
    validate_baudrate(config.get("baudrate"))
    for field, choices in (("databits", {"5", "6", "7", "8"}),
                           ("parity", {"None", "Even", "Odd", "Mark"}),
                           ("stopbits", {"1", "1.5", "2"})):
        if str(config.get(field)) not in choices:
            raise ValueError(f"无效串口参数: {field}")
    return config
