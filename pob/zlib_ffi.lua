-- Prawdziwe Inflate/Deflate dla headless PoB (SimpleGraphic ma je tylko jako atrapy).
local ffi = require("ffi")
ffi.cdef[[
typedef struct {
  const unsigned char *next_in; unsigned int avail_in; unsigned long total_in;
  unsigned char *next_out; unsigned int avail_out; unsigned long total_out;
  const char *msg; void *state; void *zalloc; void *zfree; void *opaque;
  int data_type; unsigned long adler; unsigned long reserved;
} z_stream;
const char *zlibVersion(void);
int inflateInit2_(z_stream *s, int windowBits, const char *version, int size);
int inflate(z_stream *s, int flush);
int inflateEnd(z_stream *s);
int deflateInit_(z_stream *s, int level, const char *version, int size);
int deflate(z_stream *s, int flush);
int deflateEnd(z_stream *s);
]]
-- W obrazie Docker jest tylko libz.so.1 (bez symlinku libz.so z pakietu -dev), więc próbujemy kilku nazw
local z
for _, name in ipairs({ "z", "libz.so.1", "libz.so", "zlib1", "libz.1.dylib" }) do
	local ok, lib = pcall(ffi.load, name)
	if ok then z = lib; break end
end
assert(z, "nie znaleziono biblioteki zlib")
local CHUNK = 4 * 1024 * 1024
local Z_FINISH, Z_STREAM_END, Z_NO_FLUSH = 4, 1, 0

local function run(init, step, finish, data, flush)
  local s = ffi.new("z_stream")
  local rc = init(s)
  if rc ~= 0 then return nil, "init " .. rc end
  local buf = ffi.new("unsigned char[?]", CHUNK)
  local parts = {}
  s.next_in = ffi.cast("const unsigned char*", data)
  s.avail_in = #data
  while true do
    s.next_out = buf
    s.avail_out = CHUNK
    local r = step(s, flush)
    local produced = CHUNK - s.avail_out
    if produced > 0 then parts[#parts + 1] = ffi.string(buf, produced) end
    if r == Z_STREAM_END then break end
    if r ~= 0 then finish(s); return nil, "zlib error " .. r end
    if s.avail_in == 0 and produced == 0 then break end
  end
  finish(s)
  return table.concat(parts)
end

function Inflate(data)
  local ver = z.zlibVersion()
  local out, err = run(function(s) return z.inflateInit2_(s, 15 + 32, ver, ffi.sizeof("z_stream")) end,
    z.inflate, z.inflateEnd, data, Z_NO_FLUSH)
  if not out then return nil, err end
  return out
end

function Deflate(data)
  local ver = z.zlibVersion()
  local out, err = run(function(s) return z.deflateInit_(s, 9, ver, ffi.sizeof("z_stream")) end,
    z.deflate, z.deflateEnd, data, Z_FINISH)
  if not out then return nil, err end
  return out
end
