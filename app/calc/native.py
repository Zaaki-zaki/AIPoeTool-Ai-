"""Silnik PoB bez Dockera: LuaJIT z pakietu `lupa` uruchamia pob/server.lua w tym samym protokole JSON-lines (stdin/stdout).

Użycie (robi to PobEngine): python -m app.calc.native <katalog PathOfBuilding/src>
Zamiast zewnętrznych bibliotek C: lua-utf8 zastępuje mały shim (teksty gry są ASCII), a zlib pochodzi z Pythona.
"""

from __future__ import annotations

import os
import sys
import zlib
from pathlib import Path

SERVER_LUA = Path(__file__).resolve().parents[2] / "pob" / "server.lua"

UTF8_SHIM = r"""
local u = {}
function u.find(s, p, i, plain) return string.find(s, p, i, plain) end
function u.gsub(s, p, r, n) return string.gsub(s, p, r, n) end
function u.match(s, p, i) return string.match(s, p, i) end
function u.sub(s, i, j) return string.sub(s, i, j) end
function u.reverse(s) return string.reverse(s) end
function u.len(s) return #s end
function u.lower(s) return string.lower(s) end
function u.upper(s) return string.upper(s) end
function u.char(...) return string.char(...) end
function u.next(s, i, n)
	i = (i or 0) + (n or 1)
	if i < 1 or i > #s then return nil end
	return i
end
function u.gmatch(s, p) return string.gmatch(s, p) end
return u
"""

# PoB używa składni "x += 1" (nowszy LuaJIT). LuaJIT z lupy jej nie zna, więc ładujemy moduły przez prosty przepis na "x = x + (1)".
COMPOUND_LUA = r"""
local function fix(text)
	if not text:find("[%+%-%*/%%%^]=") and not text:find("%.%.=") then return text end
	local out = {}
	for line in (text .. "\n"):gmatch("(.-)\n") do
		local ind, lhs, op, rhs = line:match("^(%s*)([%a_][%w_%.]*[%w_%]]?)%s*([%+%-%*/%%%^])=%s*(.-)%s*$")
		if not ind then ind, lhs, rhs = line:match("^(%s*)([%a_][%w_%.]*[%w_%]]?)%s*%.%.=%s*(.-)%s*$"); op = ".." end
		if ind and lhs and not rhs:find("^%-%-") then line = ind .. lhs .. " = " .. lhs .. " " .. op .. " (" .. rhs .. ")" end
		out[#out + 1] = line
	end
	return table.concat(out, "\n")
end
local rawloadfile = loadfile
function loadfile(name, ...)
	if type(name) ~= "string" then return rawloadfile(name, ...) end
	local fh = io.open(name, "rb")
	if not fh then return nil, "cannot open " .. name end
	local text = fh:read("*a"); fh:close()
	return loadstring(fix(text), "@" .. name)
end
function dofile(name)
	local f, err = loadfile(name)
	if not f then error(err, 2) end
	return f()
end
"""

# Protokół przez Pythona, nie przez stdio biblioteki C z LuaJIT (na Windowsie ucinało długie linie): io.lines() i io.write() obsługuje Python.
IO_LUA = r"""
local real_lines = io.lines
io.lines = function(name, ...)
	if name then return real_lines(name, ...) end
	return function() return py_readline() end
end
io.write = function(...) py_write(...) end
"""

ZLIB_LUA = r"""
function Inflate(data) return py_inflate(data) end
function Deflate(data) return py_deflate(data) end
"""


def _inflate(data: bytes) -> bytes | None:
    try:
        return zlib.decompress(data, 15 + 32)
    except zlib.error:
        return None


def _deflate(data: bytes) -> bytes:
    return zlib.compress(data, 9)


def _readline() -> bytes | None:
    line = sys.stdin.buffer.readline()
    return line.rstrip(b"\r\n") if line else None


def _write(*parts: bytes) -> None:
    out = sys.stdout.buffer
    for p in parts:
        out.write(p if isinstance(p, bytes) else str(p).encode("utf-8"))
    out.flush()


def main(src_dir: str) -> None:
    try:
        from lupa.luajit21 import LuaRuntime
    except ImportError:
        from lupa import LuaRuntime  # type: ignore[assignment]
    src = Path(src_dir).resolve()
    if not (src / "HeadlessWrapper.lua").exists():
        sys.exit(f"Brak PoB w {src} (HeadlessWrapper.lua). Uruchom: uv run python -m app.calc.setup")
    os.chdir(src)
    zlib_file = src.parent / "aipoe_zlib.lua"  # server.lua wstrzykuje ten plik zaraz po _SimpleGraphic.def.lua
    zlib_file.write_text(ZLIB_LUA, encoding="utf-8")
    os.environ["POB_ZLIB_LUA"] = str(zlib_file).replace("\\", "/")
    lua = LuaRuntime(encoding=None, unpack_returned_tuples=True)
    g = lua.globals()
    g.py_inflate, g.py_deflate = _inflate, _deflate
    g.py_readline, g.py_write = _readline, _write
    runtime = (src.parent / "runtime" / "lua").as_posix()
    lua.execute(f"package.path = '{runtime}/?.lua;{runtime}/?/init.lua;' .. package.path")
    lua.execute("package.preload['lua-utf8'] = function() " + UTF8_SHIM + " end")
    lua.execute("arg = {[0] = \"server.lua\"}")
    lua.execute(COMPOUND_LUA)
    lua.execute(IO_LUA)
    code = SERVER_LUA.read_bytes()
    lua.execute(code)


if __name__ == "__main__":
    main(sys.argv[1])
