import re
import subprocess

with open('bundle.js', 'r') as f:
    code = f.read()

# 1. Update saveCloudSyncRecord and getCloudSyncState
p_sync = code.find('async function saveCloudSyncRecord')
p_vp = code.find('async function Vp', p_sync)

new_cloud_sync = """async function saveCloudSyncRecord(title, payload) {
  try {
    let tUser = null;
    try { const { data: t } = await L.auth.getUser(); if (t && t.user) tUser = t.user; } catch(e) {}
    if (!tUser) {
      try { const { data: s } = await L.auth.getSession(); if (s && s.session && s.session.user) tUser = s.session.user; } catch(e) {}
    }
    const isUUID = str => typeof str === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(str);
    const syncUid = (tUser?.id && isUUID(tUser.id)) ? tUser.id : "54d69b2e-76f7-410d-84fc-af00f7101786";
    
    await L.from("listings").insert({
      user_id: syncUid,
      title: title,
      category_id: "3ed03846-ea53-4f52-9db5-17550b75f3f2",
      location_id: "02ef9e15-c49f-459e-916c-2432e90dd230",
      price: 0,
      condition: "new",
      description: JSON.stringify(payload),
      phone: "9876543210",
      whatsapp: "9876543210",
      images: [],
      status: "active",
      is_featured: false
    });
    _lastCloudSyncFetchTime = 0;
  } catch(err) {
    console.warn("saveCloudSyncRecord error:", err);
  }
}

async function getCloudSyncState(forceFresh = false) {
  const now = Date.now();
  if (!forceFresh && _cachedCloudSync && (now - _lastCloudSyncFetchTime < CLOUD_SYNC_CACHE_TTL)) {
    return _cachedCloudSync;
  }
  
  let deletedListingIds = [];
  let listingStatusOverrides = {};
  let userStatusOverrides = {};
  let rechargeStatusOverrides = {};
  let cloudRechargeRequests = [];
  let cloudTransactions = [];
  let cloudConfig = {};

  try {
    const { data: sysRows } = await L.from("listings")
      .select("title, description, created_at")
      .in("title", [
        "[SYS_DELETED_LISTING]",
        "[SYS_LISTING_STATUS]",
        "[SYS_USER_STATUS]",
        "[SYS_RECHARGE_STATUS]",
        "[SYS_RECHARGE_REQUEST]",
        "[SYS_TOP_PRO_REQUEST]",
        "[SYS_TRANSACTION]",
        "[SYS_APP_CONFIG]"
      ])
      .order("created_at", { ascending: false })
      .limit(600);

    if (sysRows && Array.isArray(sysRows)) {
      sysRows.forEach(row => {
        if (!row || !row.description) return;
        try {
          const parsed = JSON.parse(row.description);
          if (!parsed) return;
          
          if (row.title === "[SYS_DELETED_LISTING]") {
            if (parsed.deleted_id && !deletedListingIds.includes(parsed.deleted_id)) {
              deletedListingIds.push(parsed.deleted_id);
            }
            if (Array.isArray(parsed.deleted_listing_ids)) {
              parsed.deleted_listing_ids.forEach(dId => {
                if (dId && !deletedListingIds.includes(dId)) deletedListingIds.push(dId);
              });
            }
          } else if (row.title === "[SYS_LISTING_STATUS]") {
            const lId = parsed.listing_id;
            if (lId && !listingStatusOverrides[lId]) {
              listingStatusOverrides[lId] = {
                status: parsed.status,
                is_featured: parsed.is_featured,
                updated_at: parsed.updated_at || row.created_at
              };
            }
          } else if (row.title === "[SYS_USER_STATUS]") {
            const uId = parsed.user_id;
            const uEmail = (parsed.user_email || "").trim().toLowerCase();
            const uData = {
              account_status: parsed.account_status || parsed.status,
              status: parsed.status || parsed.account_status,
              is_pro: parsed.is_pro,
              pro_status: parsed.pro_status,
              pro_expires_at: parsed.pro_expires_at || parsed.approved_expiry_date,
              approved_expiry_date: parsed.approved_expiry_date || parsed.pro_expires_at,
              updated_at: parsed.updated_at || row.created_at
            };
            if (uId && !userStatusOverrides[uId]) userStatusOverrides[uId] = uData;
            if (uEmail && !userStatusOverrides[uEmail]) userStatusOverrides[uEmail] = uData;
          } else if (row.title === "[SYS_RECHARGE_STATUS]") {
            const reqId = parsed.req_id;
            const utr = (parsed.utr || "").trim().toLowerCase();
            if (reqId && !rechargeStatusOverrides[reqId]) rechargeStatusOverrides[reqId] = parsed;
            if (utr && !rechargeStatusOverrides[utr]) rechargeStatusOverrides[utr] = parsed;
            if (parsed.status === "approved" && (parsed.user_id || parsed.user_email)) {
              const uId = parsed.user_id;
              const uEmail = (parsed.user_email || "").trim().toLowerCase();
              const exp = parsed.approved_expiry_date || new Date(Date.now() + 30 * 86400000).toISOString();
              const proData = {
                is_pro: true,
                pro_status: "active",
                pro_expires_at: exp,
                approved_expiry_date: exp,
                account_status: "active",
                status: "active",
                updated_at: parsed.reviewed_at || row.created_at
              };
              if (uId && !userStatusOverrides[uId]) userStatusOverrides[uId] = proData;
              if (uEmail && !userStatusOverrides[uEmail]) userStatusOverrides[uEmail] = proData;
            }
          } else if (row.title === "[SYS_RECHARGE_REQUEST]" || row.title === "[SYS_TOP_PRO_REQUEST]") {
            const isTop = row.title === "[SYS_TOP_PRO_REQUEST]" || parsed.is_top_pro || parsed.type === "top_pro_boost";
            const reqId = parsed.id || ("req_" + (parsed.utr || row.created_at));
            const reqItem = {
              ...parsed,
              id: reqId,
              is_top_pro: isTop,
              type: isTop ? "top_pro_boost" : (parsed.type || "monthly_plan"),
              created_at: parsed.created_at || parsed.submitted_at || row.created_at,
              submitted_at: parsed.submitted_at || parsed.created_at || row.created_at
            };
            if (!cloudRechargeRequests.some(r => r.id === reqId || (parsed.utr && r.utr && r.utr.toLowerCase().trim() === parsed.utr.toLowerCase().trim()))) {
              cloudRechargeRequests.push(reqItem);
            }
          } else if (row.title === "[SYS_TRANSACTION]") {
            if (parsed && (parsed.id || parsed.utr)) {
              cloudTransactions.push(parsed);
            }
          } else if (row.title === "[SYS_APP_CONFIG]") {
            if (!cloudConfig.updated_at) cloudConfig = parsed;
          }
        } catch(e) {}
      });

      // Write fresh authoritative cloud data to local cache
      try { localStorage.setItem("deleted_listing_ids", JSON.stringify(deletedListingIds)); } catch(e) {}
      try { localStorage.setItem("listing_status_overrides", JSON.stringify(listingStatusOverrides)); } catch(e) {}
      try { localStorage.setItem("admin_status_overrides", JSON.stringify(userStatusOverrides)); } catch(e) {}
      try { localStorage.setItem("recharge_status_overrides", JSON.stringify(rechargeStatusOverrides)); } catch(e) {}
    }
  } catch(err) {
    console.warn("getCloudSyncState fetch error:", err);
  }

  _cachedCloudSync = {
    deletedListingIds,
    listingStatusOverrides,
    userStatusOverrides,
    rechargeStatusOverrides,
    rechargeRequests: cloudRechargeRequests,
    cloudTransactions,
    cloudConfig
  };
  _lastCloudSyncFetchTime = Date.now();
  return _cachedCloudSync;
}
"""

code = code[:p_sync] + new_cloud_sync + code[p_vp:]
print("Updated CloudSync successfully!")

with open('bundle.js', 'w') as f:
    f.write(code)
