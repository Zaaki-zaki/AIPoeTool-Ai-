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
-- stan próbnych zmian (itemy, drzewko); czyszczony przy każdym wczytaniu buildu
local trial = { origSel = {}, trialId = {} }
function handlers.load(req)
	trial = { origSel = {}, trialId = {} }
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

-- Próbna podmiana itemu w slocie. Oryginał zapamiętujemy przy pierwszej próbie (reset_item go przywraca);
-- kolejne próby nadpisują ten sam próbny item, więc lista przedmiotów PoB nie rośnie.
local function attrs()
	local out = build.calcsTab.mainOutput
	return { Str = out.Str or 0, Dex = out.Dex or 0, Int = out.Int or 0 }
end
function handlers.set_item(req)
	local slot = build.itemsTab.slots[req.slot]
	if not slot or slot.nodeId then return nil, "nieznany slot: " .. tostring(req.slot) end
	if trial.origSel[req.slot] == nil then trial.origSel[req.slot] = slot.selItemId or 0 end
	local item = new("Item"):Item(req.text)
	if not item.base then return nil, "nie rozpoznano bazy itemu" end
	if not build.itemsTab:IsItemValidForSlot(item, req.slot) then
		return nil, "item (" .. tostring(item.type) .. ") nie pasuje do slotu " .. req.slot
	end
	local cur = build.itemsTab.items[trial.origSel[req.slot]]
	if req.slot == "Weapon 1" and cur and cur.type ~= item.type then
		return nil, "inny typ broni niż obecna (" .. tostring(cur.type) .. " vs " .. tostring(item.type) .. ")"
	end
	if trial.trialId[req.slot] then item.id = trial.trialId[req.slot] end
	build.itemsTab:AddItem(item, true)
	trial.trialId[req.slot] = item.id
	slot:SetSelItemId(item.id)
	build.buildFlag = true
	settle()
	local r = item.requirements or {}
	return {
		stats = stats(), attrs = attrs(), item_name = item.name,
		reqs = { str = r.str or 0, dex = r.dex or 0, int = r.int or 0, level = r.level or 0 },
	}
end
function handlers.reset_item(req)
	local slot = build.itemsTab.slots[req.slot]
	if not slot or slot.nodeId then return nil, "nieznany slot: " .. tostring(req.slot) end
	if trial.origSel[req.slot] ~= nil then
		slot:SetSelItemId(trial.origSel[req.slot])
		build.buildFlag = true
		settle()
	end
	return { stats = stats(), attrs = attrs() }
end
function handlers.attrs(req) return { attrs = attrs() } end

-- Drzewko pasywne. Stan wyjściowy (zbiór zaalokowanych nodów) zapamiętujemy przy pierwszym użyciu;
-- każde tree_try zaczyna od jego przywrócenia, a tree_reset kończy próby.
local function restoreTree()
	local spec = build.spec
	if not trial.treeSnap then
		trial.treeSnap = {}
		for id in pairs(spec.allocNodes) do trial.treeSnap[id] = true end
		trial.masterySnap = {}
		for id, eff in pairs(spec.masterySelections) do trial.masterySnap[id] = eff end
		return
	end
	for id in pairs(spec.masterySelections) do
		if trial.masterySnap[id] == nil then
			local mn = spec.nodes[id]
			spec.masterySelections[id] = nil
			if mn then spec:AddMasteryEffectOptionsToNode(mn) end
		end
	end
	for id, eff in pairs(trial.masterySnap) do spec.masterySelections[id] = eff end
	for id, node in pairs(spec.nodes) do node.alloc = trial.treeSnap[id] == true end
	for id in pairs(spec.allocNodes) do spec.allocNodes[id] = nil end
	for id in pairs(trial.treeSnap) do spec.allocNodes[id] = spec.nodes[id] end
	spec:BuildAllDependsAndPaths()
end
local function safeToRemove(node)
	-- usuwamy tylko zwykłe nody głównego drzewka albo własnej ascendancy; mastery, sockety i cluster jewele pomijamy
	for _, d in ipairs(node.depends) do
		if d.alloc then
			if d.type == "Mastery" or d.type == "Socket" or d.type == "ClassStart" or d.type == "AscendClassStart"
				or d.expansionJewel or d.isJewelSocket or d.id >= 65536 or d.isMultipleChoiceOption
				or d.ascendancyName ~= node.ascendancyName then
				return false
			end
		end
	end
	return true
end
-- ścieżka do nodu z głównego drzewka nie może przechodzić przez nody ascendancy (to byłyby darmowe punkty ascendancy)
local function pathCrossesAscendancy(n)
	if n.ascendancyName ~= nil then return false end
	for _, p in ipairs(n.path or {}) do
		if p.ascendancyName ~= nil then return true end
	end
	return false
end
local function pickMastery(spec, nodeId, effectId)
	local n = spec.nodes[nodeId]
	if not n or n.type ~= "Mastery" then return nil, "to nie mastery: " .. tostring(nodeId) end
	local eff = spec.tree.masteryEffects[effectId]
	if not eff then return nil, "nieznany efekt mastery: " .. tostring(effectId) end
	n.sd = eff.sd
	n.allMasteryOptions = false
	spec.tree:ProcessStats(n)
	spec.masterySelections[nodeId] = effectId
	return true
end
function handlers.tree_info(req)
	restoreTree()
	local spec = build.spec
	local maxd = req.max_dist or 6
	local myAsc = spec.curAscendClassName
	if myAsc == "None" then myAsc = nil end
	local add, remove = {}, {}
	for id, n in pairs(spec.nodes) do
		-- nody głównego drzewka oraz własnej ascendancy (inne ascendancy są nieosiągalne)
		local pool = (n.ascendancyName == nil) and "main" or ((n.ascendancyName == myAsc) and "asc" or nil)
		if id < 65536 and pool and not n.isMultipleChoiceOption then
			if not n.alloc then
				if n.path and pathCrossesAscendancy(n) then
					-- pomijamy: nieosiągalne bez punktów ascendancy
				elseif n.type == "Mastery" and n.masteryEffects and n.path and n.pathDist <= maxd then
					local taken = {}
					for _, e in pairs(spec.masterySelections) do taken[e] = true end
					local opts = {}
					for _, e in pairs(n.masteryEffects) do
						if not taken[e.effect] then opts[#opts + 1] = { effect = e.effect, stats = e.stats } end
					end
					table.sort(opts, function(a, b) return a.effect < b.effect end)
					if #opts > 0 then
						add[#add + 1] = { id = id, name = n.dn or n.name, type = "Mastery", dist = n.pathDist, pool = pool, opts = opts }
					end
				elseif (n.type == "Notable" or n.type == "Keystone") and n.path and n.pathDist <= maxd
					and #n.intuitiveLeapLikesAffecting == 0 then
					add[#add + 1] = { id = id, name = n.dn, type = n.type, dist = n.pathDist, sd = n.sd, pool = pool }
				end
			elseif (n.type == "Notable" or n.type == "Keystone" or n.type == "Normal") and safeToRemove(n) then
				local freed = 0
				for _, d in ipairs(n.depends) do if d.alloc then freed = freed + 1 end end
				if freed >= 1 and freed <= (req.max_freed or 4) then
					remove[#remove + 1] = { id = id, name = n.dn, type = n.type, freed = freed, sd = n.sd, pool = pool }
				end
			end
		end
	end
	local used, ascUsed = spec:CountAllocNodes()
	return { add = add, remove = remove, points_used = used, asc_used = ascUsed, asc_name = myAsc, stats = stats() }
end
function handlers.tree_try(req)
	restoreTree()
	local spec = build.spec
	local before, ascBefore = spec:CountAllocNodes()
	for _, id in ipairs(req.dealloc or {}) do
		local n = spec.nodes[id]
		if not n or not n.alloc then return nil, "node nie jest zaalokowany: " .. tostring(id) end
		spec:DeallocNode(n)
	end
	for _, id in ipairs(req.alloc or {}) do
		local n = spec.nodes[id]
		if not n then return nil, "nieznany node: " .. tostring(id) end
		if n.alloc then return nil, "node jest już zaalokowany: " .. tostring(id) end
		if not n.path then return nil, "brak ścieżki do nodu " .. tostring(id) end
		if pathCrossesAscendancy(n) then return nil, "ścieżka przechodzi przez ascendancy: " .. tostring(id) end
		local me = req.mastery and req.mastery[tostring(id)]
		if me then
			local ok, err = pickMastery(spec, id, me)
			if not ok then return nil, err end
		end
		spec:AllocNode(n)
	end
	build.buildFlag = true
	settle()
	-- które nody doszły/odeszły względem stanu wyjściowego (do podglądu drzewka)
	local added, removed = {}, {}
	for id in pairs(spec.allocNodes) do if not trial.treeSnap[id] then added[#added + 1] = id end end
	for id in pairs(trial.treeSnap) do if not spec.allocNodes[id] then removed[#removed + 1] = id end end
	table.sort(added); table.sort(removed)
	local now, ascNow = spec:CountAllocNodes()
	return { stats = stats(), attrs = attrs(), points_delta = now - before, asc_delta = ascNow - ascBefore,
		added = added, removed = removed }
end
local TYPE_CHAR = { Normal = "N", Notable = "T", Keystone = "K", Mastery = "M", Socket = "S",
	ClassStart = "C", AscendClassStart = "A" }
function handlers.tree_layout(req)
	restoreTree()
	local spec = build.spec
	local nodes, links, alloc = {}, {}, {}
	for id, n in pairs(spec.nodes) do
		if n.x and n.y and not (n.group and n.group.isProxy) and not n.isProxy then
			local desc
			if n.sd and #n.sd > 0 then
				desc = {}
				for i = 1, math.min(#n.sd, 8) do desc[i] = tostring(n.sd[i]) end
			end
			nodes[#nodes + 1] = {
				id = id, x = math.floor(n.x + 0.5), y = math.floor(n.y + 0.5), t = TYPE_CHAR[n.type] or "N",
				n = n.dn or n.name, a = n.ascendancyName ~= nil, asc = n.ascendancyName, d = desc, ic = n.icon,
			}
			if n.type == "Mastery" and n.masteryEffects then
				local me = {}
				for _, e in pairs(n.masteryEffects) do me[#me + 1] = { e = e.effect, s = e.stats } end
				table.sort(me, function(a, b) return a.e < b.e end)
				nodes[#nodes].me = me
			end
			if n.alloc then alloc[#alloc + 1] = id end
			for _, o in ipairs(n.linked or {}) do
				-- połączenia między drzewem głównym a ascendancy (albo między różnymi ascendancy) nie istnieją w grze
				local sameTree = (n.ascendancyName or "") == (o.ascendancyName or "")
				if o.id > id and sameTree and o.x and o.y and not (o.group and o.group.isProxy) and not o.isProxy then
					if n.group and n.group == o.group and n.orbit and n.orbit ~= 0 and n.orbit == o.orbit then
						-- połączenie po okręgu grupy: łuk (środek i promień), nie cięciwa
						local gx, gy = n.group.x, n.group.y
						links[#links + 1] = { id, o.id, math.floor(gx + 0.5), math.floor(gy + 0.5),
							math.floor(math.sqrt((n.x - gx) ^ 2 + (n.y - gy) ^ 2) + 0.5) }
					else
						links[#links + 1] = { id, o.id }
					end
				end
			end
		end
	end
	table.sort(nodes, function(a, b) return a.id < b.id end)
	table.sort(alloc)
	return { nodes = nodes, links = links, alloc = alloc, tree_version = spec.treeVersion }
end

-- Trwałe dodanie nodów (planer ścieżki rozwoju): alokuje po kolei z potrzebnymi ścieżkami i staje się nowym stanem
-- wyjściowym. Zwraca id nodów w kolejności brania (najbliższy istniejącemu drzewku pierwszy).
function handlers.tree_commit(req)
	restoreTree()
	local spec = build.spec
	local order = {}
	for _, id in ipairs(req.alloc or {}) do
		local n = spec.nodes[id]
		if not n then return nil, "nieznany node: " .. tostring(id) end
		if not n.alloc then
			if not n.path then return nil, "brak ścieżki do nodu " .. tostring(id) end
			if pathCrossesAscendancy(n) then return nil, "ścieżka przechodzi przez ascendancy: " .. tostring(id) end
			local me = req.mastery and req.mastery[tostring(id)]
			if me then
				local ok, err = pickMastery(spec, id, me)
				if not ok then return nil, err end
			end
			for i = #n.path, 1, -1 do
				local p = n.path[i]
				if not p.alloc then order[#order + 1] = p.id end
			end
			spec:AllocNode(n)
		end
	end
	build.buildFlag = true
	settle()
	trial.treeSnap = {}
	for id in pairs(spec.allocNodes) do trial.treeSnap[id] = true end
	trial.masterySnap = {}
	for id, eff in pairs(spec.masterySelections) do trial.masterySnap[id] = eff end
	return { stats = stats(), attrs = attrs(), order = order, masteries = trial.masterySnap }
end
function handlers.classes(req)
	local out = {}
	for cid, c in pairs(build.spec.tree.classes) do
		local asc = {}
		for aid, a in pairs(c.classes or {}) do asc[#asc + 1] = { id = aid, name = a.name } end
		table.sort(asc, function(x, y) return x.id < y.id end)
		out[#out + 1] = { id = cid, name = c.name, asc = asc }
	end
	table.sort(out, function(x, y) return x.id < y.id end)
	return { classes = out, tree_version = build.spec.treeVersion }
end
function handlers.tree_reset(req)
	restoreTree()
	build.buildFlag = true
	settle()
	return { stats = stats(), attrs = attrs() }
end


-- ---------------------------------------------------------------- jewele
local function jewelNodeLabel(id, node)
	if id == 26725 then return "Marauder" elseif id == 54127 then return "Duelist" elseif id == 7960 then return "Templar/Witch" end
	local best, bd = "Unknown", math.huge
	local tn = build.spec.tree.nodes[id]
	for _, r in pairs((tn and tn.nodesInRadius and tn.nodesInRadius[3]) or {}) do
		if r.isKeystone then
			local d = math.sqrt((r.x - node.x) ^ 2 + (r.y - node.y) ^ 2)
			if d < bd then best, bd = r.name, d end
		end
	end
	return best
end
local TL_NAMES = { "Glorious Vanity", "Lethal Pride", "Brutal Restraint", "Militant Faith", "Elegant Hubris" }
local function itemBrief(item)
	if not item then return nil end
	local out = { id = item.id, name = item.name or item.title, base = item.baseName, rarity = item.rarity, raw = item.raw }
	local cb = item.jewelData and item.jewelData.conqueredBy
	if cb then
		out.timeless = { seed = cb.id, conqueror = cb.conqueror and cb.conqueror.type }
	end
	local lines = {}
	for _, l in ipairs(item.explicitModLines or {}) do lines[#lines + 1] = l.line end
	for _, l in ipairs(item.implicitModLines or {}) do lines[#lines + 1] = l.line end
	out.mods = lines
	return out
end
function handlers.jewel_info(req)
	restoreTree()
	local spec = build.spec
	local sockets, equipped = {}, {}
	for id, n in pairs(spec.nodes) do
		if n.isJewelSocket and id < 65536 and n.name ~= "Charm Socket" and n.x then
			local ctl = build.itemsTab.sockets[id]
			local item = ctl and ctl.selItemId and ctl.selItemId > 0 and build.itemsTab.items[ctl.selItemId] or nil
			if item then equipped[item.id] = true end
			sockets[#sockets + 1] = {
				id = id, x = math.floor(n.x + 0.5), y = math.floor(n.y + 0.5), alloc = n.alloc == true,
				dist = (not n.alloc and n.pathDist and n.pathDist < 1000) and n.pathDist or nil,
				near = jewelNodeLabel(id, n), item = itemBrief(item),
			}
		end
	end
	table.sort(sockets, function(a, b) return a.id < b.id end)
	local spare = {}
	for id, it in pairs(build.itemsTab.items) do
		if it.type == "Jewel" and not equipped[id] and not (it.title and it.title:find("Trial")) then spare[#spare + 1] = itemBrief(it) end
	end
	table.sort(spare, function(a, b) return tostring(a.name) < tostring(b.name) end)
	return { sockets = sockets, spare = spare, stats = stats() }
end
local function ensureSocket(id, alloc)
	local spec = build.spec
	local n = spec.nodes[id]
	if not n or not n.isJewelSocket then return nil, "to nie jest socket jewela: " .. tostring(id) end
	local ctl = build.itemsTab.sockets[id]
	if not ctl then return nil, "brak slotu dla socketu " .. tostring(id) end
	if not n.alloc then
		if not alloc then return nil, "socket " .. tostring(id) .. " nie jest zaalokowany" end
		if not n.path then return nil, "brak ścieżki do socketu " .. tostring(id) end
		if pathCrossesAscendancy(n) then return nil, "ścieżka do socketu przechodzi przez ascendancy" end
		spec:AllocNode(n)
	end
	return ctl
end
local function rememberSocket(id, ctl)
	trial.jOrig = trial.jOrig or {}
	if trial.jOrig[id] == nil then trial.jOrig[id] = ctl.selItemId or 0 end
end
function handlers.jewel_trial(req)
	restoreTree()
	local ctl, err = ensureSocket(req.socket, req.alloc)
	if not ctl then return nil, err end
	rememberSocket(req.socket, ctl)
	trial.jTrialId = trial.jTrialId or {}
	if req.text and req.text ~= json.null then
		local item = new("Item"):Item(req.text)
		if not item.base then return nil, "nie rozpoznano jewela" end
		if not build.itemsTab:IsItemValidForSlot(item, ctl.slotName) then return nil, "jewel nie pasuje do tego socketu" end
		if trial.jTrialId[req.socket] then item.id = trial.jTrialId[req.socket] end
		build.itemsTab:AddItem(item, true)
		trial.jTrialId[req.socket] = item.id
		ctl:SetSelItemId(item.id)
	else
		ctl:SetSelItemId(0)
	end
	build.buildFlag = true
	settle()
	local item = ctl.selItemId and ctl.selItemId > 0 and build.itemsTab.items[ctl.selItemId] or nil
	local used, ascUsed = build.spec:CountAllocNodes()
	return { stats = stats(), attrs = attrs(), item = itemBrief(item), points_used = used }
end
function handlers.jewel_swap(req)
	restoreTree()
	local a, err = ensureSocket(req.a, req.alloc)
	if not a then return nil, err end
	local b, err2 = ensureSocket(req.b, req.alloc)
	if not b then return nil, err2 end
	rememberSocket(req.a, a); rememberSocket(req.b, b)
	local ia, ib = a.selItemId or 0, b.selItemId or 0
	local itA, itB = build.itemsTab.items[ia], build.itemsTab.items[ib]
	if itB and not build.itemsTab:IsItemValidForSlot(itB, a.slotName) then return nil, "jewel nie pasuje do socketu A" end
	if itA and not build.itemsTab:IsItemValidForSlot(itA, b.slotName) then return nil, "jewel nie pasuje do socketu B" end
	a:SetSelItemId(ib); b:SetSelItemId(ia)
	build.buildFlag = true
	settle()
	return { stats = stats(), attrs = attrs(), a = itemBrief(itB), b = itemBrief(itA) }
end
function handlers.jewel_reset(req)
	for id, orig in pairs(trial.jOrig or {}) do
		local ctl = build.itemsTab.sockets[id]
		if ctl then ctl:SetSelItemId(orig) end
	end
	trial.jOrig, trial.jTrialId = {}, {}
	restoreTree()
	build.buildFlag = true
	settle()
	return { stats = stats(), attrs = attrs() }
end
function handlers.jewel_commit(req)
	-- obecny stan (jewele i drzewko) staje się stanem wyjściowym
	trial.jOrig, trial.jTrialId = {}, {}
	local spec = build.spec
	trial.treeSnap = {}
	for id in pairs(spec.allocNodes) do trial.treeSnap[id] = true end
	trial.masterySnap = {}
	for id, eff in pairs(spec.masterySelections) do trial.masterySnap[id] = eff end
	return { stats = stats() }
end

-- Wyszukiwarka seedów Timeless Jewel na danych PoB (LUT). Ocena: suma wag pasujących fraz w statach nodów,
-- które jewel podmieni/rozszerzy w zasięgu socketu (nody zaalokowane; opcjonalnie niezaalokowane w pobliżu z wagą 0.3).
function handlers.tl_search(req)
	restoreTree()
	local spec = build.spec
	local jt = req.type
	if not TL_NAMES[jt] then return nil, "nieznany typ jewela: " .. tostring(jt) end
	local node = spec.nodes[req.socket]
	if not node or not node.isJewelSocket then return nil, "to nie jest socket jewela: " .. tostring(req.socket) end
	local tn = spec.tree.nodes[req.socket]
	local radius = tn and tn.nodesInRadius and tn.nodesInRadius[3]
	if not radius then return nil, "brak danych zasięgu socketu" end
	local wanted = {}
	for _, w in ipairs(req.wanted or {}) do
		wanted[#wanted + 1] = { pat = tostring(w.text):lower(), w = tonumber(w.weight) or 1 }
	end
	if #wanted == 0 then return nil, "podaj co najmniej jedną szukaną statystykę" end
	local legion = spec.tree.legion
	local adds = data.timelessJewelAdditions
	local function textOf(n) return n and table.concat(n.sd or {}, "\n"):lower() or "" end
	local function scoreText(t)
		local sc, hit = 0, {}
		for _, w in ipairs(wanted) do
			if t:find(w.pat, 1, true) then sc = sc + w.w; hit[#hit + 1] = w.pat end
		end
		return sc, hit
	end
	local near = req.near or 0
	local targets = {}
	for id, r in pairs(radius) do
		if r.type == "Notable" and not r.isJewelSocket and not r.ascendancyName then
			local sn = spec.nodes[id]
			local w
			if sn and sn.alloc then w = 1 elseif sn and near > 0 and sn.path and sn.pathDist and sn.pathDist <= near then w = 0.3 end
			if w then
				local orig = scoreText(table.concat(r.sd or {}, "\n"):lower())
				targets[#targets + 1] = { id = id, name = r.dn or r.name, w = w, orig = orig }
			end
		end
	end
	if #targets == 0 then return { results = {}, scanned = 0, targets = 0, note = "brak notable'i w zasięgu tego socketu" } end
	local cache = {}
	local function nodeScore(kind, idx)
		local key = kind .. idx
		local c = cache[key]
		if not c then
			local n = (kind == "r") and legion.nodes[idx] or legion.additions[idx]
			local t = textOf(n)
			local sc, hit = scoreText(t)
			local nm = n and (n.dn or n.id) or "?"
			if n and n.sd and #n.sd > 0 then nm = nm .. ": " .. table.concat(n.sd, " / "):gsub("%(%d+%-%d+%)", "X") end
			c = { sc = sc, hit = hit, name = nm }
			cache[key] = c
		end
		return c
	end
	local lo, hi = data.timelessJewelSeedMin[jt], data.timelessJewelSeedMax[jt]
	if req.seed_min then lo = math.max(lo, req.seed_min) end
	if req.seed_max then hi = math.min(hi, req.seed_max) end
	local scored = {}
	for s = lo, hi do
		local seed = (jt == 5) and s * 20 or s
		local total, hits = 0, {}
		for _, t in ipairs(targets) do
			local r = data.readLUT(seed, t.id, jt)
			if r and r[1] then
				local gain, info
				if jt == 1 and (#r == 6 or #r == 8) then
					gain = 0
					for i = 1, #r / 2 do
						local c = nodeScore("a", r[i] + 1)
						if c.sc > 0 then gain = gain + c.sc; info = (info and info .. ", " or "") .. c.name end
					end
				elseif r[1] >= adds then
					local c = nodeScore("r", r[1] + 1 - adds)
					gain = c.sc - t.orig; info = c.name
					if c.sc == 0 then info = nil end
				else
					local c = nodeScore("a", r[1] + 1)
					gain = c.sc; info = c.sc > 0 and c.name or nil
				end
				if gain and gain ~= 0 then
					total = total + gain * t.w
					if info then hits[#hits + 1] = { node = t.name, gets = info } end
				end
			end
		end
		if total > 0 then scored[#scored + 1] = { seed = seed, score = total, hits = hits } end
	end
	table.sort(scored, function(a, b) if a.score ~= b.score then return a.score > b.score end return a.seed < b.seed end)
	local top = {}
	for i = 1, math.min(req.top or 40, #scored) do top[i] = scored[i] end
	return { results = top, scanned = hi - lo + 1, targets = #targets, matched = #scored }
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
