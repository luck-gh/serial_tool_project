"""CLI 与 GUI 共用的本机端点标识和消息边界."""
import getpass
import hashlib
import json
import os

MAX_MESSAGE = 128 * 1024
MAX_CONFIG_RESULT_MESSAGE = 4 * 1024 * 1024
MAX_PENDING_OUTPUT = 4 * MAX_MESSAGE
DATA_CHUNK_SIZE = 64 * 1024


def current_user_identity():
    """读取实际 Windows 进程令牌账户/SID;其他平台保留用户名表示.

    Windows 身份不读取 USERNAME/USER 环境变量,不申请额外权限.
    """
    if os.name != "nt":
        return dict(user=getpass.getuser())
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    security.LookupAccountSidW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD)]
    token = wintypes.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        # TOKEN_USER 的首个字段是 SID_AND_ATTRIBUTES,其首字段为 SID 指针.
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        text = wintypes.LPWSTR()
        if not security.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            sid_text = text.value
        finally:
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
        user_size, domain_size, kind = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
        security.LookupAccountSidW(None, sid, None, ctypes.byref(user_size), None,
                                  ctypes.byref(domain_size), ctypes.byref(kind))
        user = ctypes.create_unicode_buffer(user_size.value)
        domain = ctypes.create_unicode_buffer(domain_size.value)
        if security.LookupAccountSidW(None, sid, user, ctypes.byref(user_size), domain,
                                      ctypes.byref(domain_size), ctypes.byref(kind)):
            name = f"{domain.value}\\{user.value}" if domain.value else user.value
        else:
            name = sid_text
        return dict(user=name, user_sid=sid_text)
    finally:
        kernel.CloseHandle(token)


def bridge_target(config_file):
    """解析两种 CLI 共用的端点与绝对配置路径,不读取配置内容."""
    path = os.path.realpath(config_file)
    user = getpass.getuser()
    identity = user + ":" + os.path.normcase(path)
    return dict(config_path=path, endpoint_user=user,
                endpoint="ghowe-cli-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32])


def bridge_connection_failure(target, socket_error, socket_error_code):
    """构造定位失败的共享诊断,不将无法连接武断归因于 GUI 未启动."""
    try:
        identity = current_user_identity()
    except OSError as exc:
        identity = dict(user="实际账户读取失败", identity_error=str(exc))
    context = dict(target, **identity, socket_error=socket_error, socket_error_code=int(socket_error_code))
    account = identity["user"] + (f" ({identity['user_sid']})" if "user_sid" in identity else "")
    message = (f"无法连接对应 GUI；实际账户: {account}；配置绝对路径: {target['config_path']}；"
               f"端点定位用户名: {target['endpoint_user']}；Qt: {socket_error}。"
               "可能原因：GUI 未启动、--config 路径不一致、或 Windows 用户不同。"
               "当前默认桥接要求 GUI 与 CLI 使用同一用户及同一配置路径。")
    return dict(event="result", success=False, status="failed", message=message, bridge=context)


def endpoint_name(config_file):
    """由用户名及规范化配置路径生成本机通信端点名称,不读取配置内容.

    Args:
        config_file (str): GUI 使用的配置路径;相对路径按调用进程工作目录解析.

    Returns:
        str: 带 ghowe-cli- 前缀的 SHA-256 截断名称;同用户,同路径定位同一窗口.
    """
    return bridge_target(config_file)["endpoint"]


def encode_json_line(message, max_bytes=MAX_MESSAGE):
    """编码一条带换行的 UTF-8 JSON 帧,按实际字节数执行上限.

    Args:
        message (dict): 要发送的 JSON 对象.
        max_bytes (int): 整帧最大字节数,包含 UTF-8 内容和末尾换行.

    Raises:
        ValueError: JSON 帧编码后超过上限.
    """
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
    if len(payload) > max_bytes:
        raise ValueError(f"JSON 帧长度 {len(payload)} 字节超过 {max_bytes} 字节上限")
    return payload
