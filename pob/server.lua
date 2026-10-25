-- Serwer obliczeń: headless PoB sterowany po stdin/stdout (jedna linia JSON = jedno polecenie).
-- Uruchamiany z katalogu PathOfBuilding/src przez:  luajit server.lua  (patrz pob/run.sh)
-- Protokół:
--   {"cmd":"load","xml":"<PathOfBuilding>..."}                        -> {"ok":true,"stats":{...},"groups":[...]}
--   {"cmd":"stats"}                                                   -> {"ok":true,"stats":{...}}
--   {"cmd":"set_group","group":3,"gems":[{"name":"Cyclone","level":21,"quality":20},...]}  (1-based)
--                                                                     -> {"ok":true,"stats":{...}}
--   {"cmd":"quit"}
local ZLIB = os.getenv("POB_ZLIB_LUA") or "/opt/pobserver/zlib_ffi.lua"
local fh = assert(io.open("HeadlessWrapper.lua", "rb")); local src = fh:read("*a"); fh:close()
src = src:gsub('dofile%("_SimpleGraphic%.def%.lua"%)',
	'dofile("_SimpleGraphic.def.lua"); dofile("' .. ZLIB .. '")', 1)
-- Logi PoB (print/ConPrintf) idą na stderr, żeby stdout zawierał tylko odpowiedzi protokołu
print = function(...) io.stderr:write(table.concat({...}, "\t"), "\n") end
assert(loadstring(src, "=HeadlessWrapper"))()
local json = require("dkjson")

local STAT_KEYS = {
	"Life", "EnergyShield", "Mana", "Armour", "Evasion", "FireResist", "ColdResist",
	"LightningResist", "ChaosResist", "TotalDPS", "CombinedDPS", "FullDPS", "Speed",
	"HitChance", "CritChance", "CritMultiplier", "TotalEHP", "PhysicalMaximumHitTaken",
	"FireMaximumHitTaken", "ColdMaximumHitTaken", "LightningMaximumHitTaken",
	"ChaosMaximumHitTaken", "ManaUnreserved", "LifeUnreserved", "AverageHit", "AverageDamage",
	"TotalDot", "WithPoisonDPS", "BleedDPS", "IgniteDPS", "PoisonDPS", "ImpaleDPS",
}

local function settle()
	local frames = 0
	while (build.buildFlag or not (build.calcsTab and build.calcsTab.mainOutput)) and frames < 600 do
		runCallback("OnFrame"); frames = frames + 1
	end
end

local function stats()
	local out = build.calcsTab.mainOutput
	local res = {}
	for _, k in ipairs(STAT_KEYS) do
		local v = out[k]
		if type(v) == "number" and v == v and v ~= math.huge and v ~= -math.huge then res[k] = v end
	end
	local mainSkill = build.calcsTab.mainEnv and build.calcsTab.mainEnv.player.mainSkill
	res.MainSkill = mainSkill and mainSkill.activeEffect and mainSkill.activeEffect.grantedEffect.name or nil
	return res
end

local function gemName(gi)
	if gi.nameSpec and gi.nameSpec ~= "" then return gi.nameSpec end
	if gi.gemData and gi.gemData.name then return gi.gemData.name end
	local ge = gi.grantedEffect or (gi.skillId and build.data.skills[gi.skillId])
	return ge and ge.name or ""
end

local function nn(x)  -- dkjson zamienia JSON null na sentinel json.null; traktujemy go jak nil
	if x == json.null then return nil end
	return x
end

local function groups()
	local list = {}
	for i, g in ipairs(build.skillsTab.socketGroupList) do
		local gems = {}
		for _, gi in ipairs(g.gemList) do
			gems[#gems + 1] = {
				name = gemName(gi), level = gi.level, quality = gi.quality, enabled = gi.enabled,
				gem_id = gi.gemId, skill_id = gi.skillId, quality_id = gi.qualityId, count = gi.count,
				enable_global1 = gi.enableGlobal1, enable_global2 = gi.enableGlobal2,
			}
		end
		list[#list + 1] = { index = i, slot = g.slot, enabled = g.enabled, source = g.source, gems = gems }
	end
	return list
end

local handlers = {}
function handlers.load(req)
	loadBuildFromXML(req.xml, "calc")
	settle()
	return { stats = stats(), groups = groups() }
end
function handlers.stats(req) return { stats = stats() } end
function handlers.groups(req) return { groups = groups() } end
function handlers.set_main(req)
	local g = build.skillsTab.socketGroupList[req.group]
	if not g then return nil, "brak grupy " .. tostring(req.group) end
	build.mainSocketGroup = req.group
	g.mainActiveSkill = req.skill_index or 1
	build.buildFlag = true
	settle()
	return { stats = stats() }
end
function handlers.set_group(req)
	local g = build.skillsTab.socketGroupList[req.group]
	if not g then return nil, "brak grupy " .. tostring(req.group) end
	if g.source then
		return nil, "grupa " .. tostring(req.group) .. " pochodzi z itemu (" .. tostring(g.source) .. ") - PoB odtwarza ją sam"
	end
	local gemList = {}
	for _, spec in ipairs(req.gems) do
		local inst = {
			nameSpec = nn(spec.name) or "", level = nn(spec.level) or 20, quality = nn(spec.quality) or 0,
			qualityId = nn(spec.quality_id) or "Default", enabled = nn(spec.enabled) ~= false,
			enableGlobal1 = nn(spec.enable_global1) ~= false, enableGlobal2 = nn(spec.enable_global2) == true,
			count = nn(spec.count) or 1,
		}
		-- gemy przyznawane przez itemy/skille specjalne identyfikuje id, nie nazwa
		if nn(spec.gem_id) then inst.gemId = spec.gem_id end
		if nn(spec.skill_id) then inst.skillId = spec.skill_id end
		gemList[#gemList + 1] = inst
	end
	g.gemList = gemList
	build.skillsTab:ProcessSocketGroup(g)
	build.buildFlag = true
	settle()
	-- błędy gemów (np. nieznana nazwa) zwracamy jawnie
	local errs = {}
	for _, gi in ipairs(g.gemList) do
		if gi.errMsg then errs[#errs + 1] = gi.errMsg end
		if not gi.gemData and not gi.grantedEffect then errs[#errs + 1] = "nie rozpoznano gemu: " .. tostring(gi.nameSpec) end
	end
	return { stats = stats(), gem_errors = errs }
end

io.stdout:setvbuf("line")
for line in io.lines() do
	local req = json.decode(line)
	if not req then
		io.write(json.encode({ ok = false, error = "niepoprawny JSON" }), "\n")
	elseif req.cmd == "quit" then
		break
	else
		local h = handlers[req.cmd]
		if not h then
			io.write(json.encode({ ok = false, error = "nieznane polecenie: " .. tostring(req.cmd) }), "\n")
		else
			local ok, res, err = pcall(h, req)
			if not ok then
				io.write(json.encode({ ok = false, error = tostring(res) }), "\n")
			elseif not res then
				io.write(json.encode({ ok = false, error = err }), "\n")
			else
				res.ok = true
				io.write(json.encode(res), "\n")
			end
		end
	end
end
