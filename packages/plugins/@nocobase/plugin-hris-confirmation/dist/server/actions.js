/**
 * HRIS 转正受控动作：saveDraft / submit / approve / reject。
 *
 * 关键不变量（全部由服务端强制执行，不接受客户端赋值）：
 * - employeeId / approverId / status / version 由服务端确定；
 * - 角色由服务端从权威角色关系（users.roles）读取，不信任客户端指定；
 * - 同一员工的并发提交、同一申请的并发决定用数据库行锁串行化；
 * - 事务内多写（申请 + 员工 + 轨迹），任一步失败整体回滚。
 *
 * 角色矩阵：
 * - saveDraft / submit：employee 或 manager（本人申请）；
 * - approve / reject：manager（被指定的直属主管审批，HR 与员工不能审批）。
 *
 * 参数约定：POST body 位于 ctx.action.params.values（兼容 ctx.request.body）。
 */
const PROTECTED_FIELDS = new Set([
  'id',
  'employee',
  'employeeId',
  'approver',
  'approverId',
  'status',
  'version',
  'submittedAt',
  'decidedAt',
  'decisionComment',
  'createdAt',
  'updatedAt',
  'createdBy',
  'createdById',
  'updatedBy',
  'updatedById',
]);

// 可发起/提交转正的角色；可审批的角色只有 manager。
const SUBMIT_ROLES = ['employee', 'manager'];
const APPROVE_ROLES = ['manager'];
const MAX_COMMENT_LENGTH = 1000;

function throwHttp(ctx, status, message) {
  ctx.throw(status, message);
}

function currentUser(ctx) {
  const user = ctx.state.currentUser;
  if (!user) {
    throwHttp(ctx, 401, '未登录');
  }
  return user;
}

function bodyOf(ctx) {
  return ctx.action.params.values || ctx.request.body || {};
}

// 从权威角色关系读取当前账号的角色名（不信任客户端指定角色）。
async function userRoleNames(db, userId, transaction) {
  const repo = db.getRepository('users');
  const user = await repo.findOne({
    filterByTk: userId,
    appends: ['roles'],
    transaction,
  });
  const roles = user && user.roles ? user.roles : [];
  return roles
    .map((r) => (typeof r === 'string' ? r : r && r.name))
    .filter(Boolean);
}

async function requireRoles(ctx, db, userId, allowed, message) {
  const names = await userRoleNames(db, userId);
  if (!allowed.some((a) => names.includes(a))) {
    throwHttp(ctx, 403, message || '当前账号角色无权执行此操作');
  }
  return names;
}

// 审批意见校验：必须是文本、trim 后非空（驳回必填）、长度受限。
function normalizeComment(ctx, raw, required) {
  if (raw === undefined || raw === null) {
    if (required) {
      throwHttp(ctx, 400, '驳回必须填写审批意见');
    }
    return null;
  }
  if (typeof raw !== 'string') {
    throwHttp(ctx, 400, '审批意见必须是文本');
  }
  const text = raw.trim();
  if (required && !text) {
    throwHttp(ctx, 400, '驳回必须填写有效的审批意见（不能为空白）');
  }
  if (text.length > MAX_COMMENT_LENGTH) {
    throwHttp(ctx, 400, `审批意见过长（最多 ${MAX_COMMENT_LENGTH} 字）`);
  }
  return text;
}

// 账号绑定的员工（应唯一）；异常数据出现多条时拒绝含糊绑定。
async function resolveEmployee(ctx, db, userId) {
  const rows = await db.getRepository('employees').find({ filter: { userId } });
  if (!rows || rows.length === 0) {
    return null;
  }
  if (rows.length > 1) {
    throwHttp(ctx, 409, '账号绑定到多个员工档案（数据异常），无法确定唯一身份');
  }
  return rows[0];
}

// 保存草稿：新建（employeeId 由绑定确定）或编辑本人草稿。
async function saveDraft(ctx, next) {
  const db = ctx.db;
  const user = currentUser(ctx);
  const input = bodyOf(ctx);
  const requestId = input.requestId;

  await requireRoles(ctx, db, user.id, SUBMIT_ROLES, '只有员工或主管可以发起转正申请');

  const reqRepo = db.getRepository('confirmation_requests');

  const employee = await resolveEmployee(ctx, db, user.id);
  if (!employee) {
    throwHttp(ctx, 403, '当前账号未绑定员工档案，无法发起转正申请');
  }
  if (employee.status !== 'PROBATION') {
    throwHttp(ctx, 400, '当前员工不是试用状态，无法新建转正申请');
  }

  // 允许的编辑内容：请求体中除受保护字段外的其余字段（支持低代码新增选填字段）。
  const content = {};
  for (const [key, value] of Object.entries(input)) {
    if (PROTECTED_FIELDS.has(key)) {
      continue;
    }
    content[key] = value;
  }

  let resultId;
  if (requestId) {
    await db.sequelize.transaction(async (transaction) => {
      const request = await reqRepo.model.findOne({
        where: { id: requestId },
        lock: transaction.LOCK.UPDATE,
        transaction,
      });
      if (!request) {
        throwHttp(ctx, 404, '申请不存在');
      }
      if (request.employeeId !== employee.id) {
        throwHttp(ctx, 403, '只能编辑本人的申请');
      }
      if (request.status !== 'DRAFT') {
        throwHttp(ctx, 400, '只有草稿状态可以编辑');
      }
      await request.update(content, { transaction });
      resultId = request.id;
    });
  } else {
    const created = await reqRepo.create({
      values: {
        ...content,
        employeeId: employee.id,
        status: 'DRAFT',
      },
    });
    resultId = created.id;
  }

  const fresh = await reqRepo.findOne({ filterByTk: resultId });
  ctx.body = fresh ? fresh.toJSON() : null;
  await next();
}

// 提交：本人 DRAFT -> PENDING，服务端确定 approver 与 submittedAt。
async function submit(ctx, next) {
  const db = ctx.db;
  const user = currentUser(ctx);
  const input = bodyOf(ctx);
  const requestId = input.requestId;
  if (!requestId) {
    throwHttp(ctx, 400, '缺少 requestId');
  }

  await requireRoles(ctx, db, user.id, SUBMIT_ROLES, '只有员工或主管可以提交转正申请');

  const empRepo = db.getRepository('employees');
  const reqRepo = db.getRepository('confirmation_requests');
  const actRepo = db.getRepository('approval_actions');
  const userRepo = db.getRepository('users');

  const employee = await resolveEmployee(ctx, db, user.id);
  if (!employee) {
    throwHttp(ctx, 403, '当前账号未绑定员工档案');
  }

  await db.sequelize.transaction(async (transaction) => {
    // 1) 行锁员工，串行化同一员工的并发提交。
    const lockedEmployee = await empRepo.model.findOne({
      where: { id: employee.id },
      lock: transaction.LOCK.UPDATE,
      transaction,
    });
    if (!lockedEmployee) {
      throwHttp(ctx, 404, '员工档案不存在');
    }
    if (lockedEmployee.status !== 'PROBATION') {
      throwHttp(ctx, 400, '当前员工不是试用状态，无法提交转正');
    }

    // 2) 行锁申请。
    const request = await reqRepo.model.findOne({
      where: { id: requestId },
      lock: transaction.LOCK.UPDATE,
      transaction,
    });
    if (!request) {
      throwHttp(ctx, 404, '申请不存在');
    }
    if (request.employeeId !== lockedEmployee.id) {
      throwHttp(ctx, 403, '只能提交本人的申请');
    }
    if (request.status !== 'DRAFT') {
      throwHttp(ctx, 400, '只有草稿可以提交');
    }

    // 3) 无其他待审批申请（员工行已锁，计数可靠）。
    const pending = await reqRepo.model.count({
      where: { employeeId: lockedEmployee.id, status: 'PENDING' },
      transaction,
    });
    if (pending > 0) {
      throwHttp(ctx, 409, '已存在待审批的转正申请，请勿重复提交');
    }

    // 4) 解析直属主管账号，并确认其当前具备审批资格（角色 manager）。
    const manager = lockedEmployee.managerId
      ? await empRepo.model.findByPk(lockedEmployee.managerId, { transaction })
      : null;
    if (!manager) {
      throwHttp(ctx, 400, '未配置直属主管，无法提交');
    }
    const managerUser = manager.userId
      ? await userRepo.model.findByPk(manager.userId, { transaction })
      : null;
    if (!managerUser) {
      throwHttp(ctx, 400, '直属主管未绑定账号，无法提交');
    }
    const mgrRoles = await userRoleNames(db, managerUser.id, transaction);
    if (!mgrRoles.includes('manager')) {
      throwHttp(ctx, 400, '直属主管账号当前无审批资格，无法提交');
    }

    // 5) 更新申请 + 追加轨迹（同一事务）。
    await request.update(
      {
        status: 'PENDING',
        approverId: managerUser.id,
        submittedAt: new Date(),
        version: (request.version || 0) + 1,
      },
      { transaction },
    );

    await actRepo.create({
      values: {
        requestId: request.id,
        actorUserId: user.id,
        action: 'SUBMIT',
        actedAt: new Date(),
      },
      transaction,
    });
  });

  const fresh = await reqRepo.findOne({ filterByTk: requestId });
  ctx.body = fresh ? fresh.toJSON() : null;
  await next();
}

// 通过 / 驳回：校验角色、approver、状态、员工任职状态，事务内更新。
async function decide(ctx, action, next) {
  const db = ctx.db;
  const user = currentUser(ctx);
  const input = bodyOf(ctx);
  const requestId = input.requestId;
  const comment = normalizeComment(ctx, input.comment, action === 'REJECT');

  if (!requestId) {
    throwHttp(ctx, 400, '缺少 requestId');
  }

  // 审批动作要求 manager 角色（员工 / HR 即使被配置为 approver 也不能审批）。
  await requireRoles(ctx, db, user.id, APPROVE_ROLES, '只有主管可以审批转正申请');

  const empRepo = db.getRepository('employees');
  const reqRepo = db.getRepository('confirmation_requests');
  const actRepo = db.getRepository('approval_actions');

  await db.sequelize.transaction(async (transaction) => {
    // 1) 行锁申请，串行化同一申请的并发决定。
    const request = await reqRepo.model.findOne({
      where: { id: requestId },
      lock: transaction.LOCK.UPDATE,
      transaction,
    });
    if (!request) {
      throwHttp(ctx, 404, '申请不存在');
    }
    if (request.status !== 'PENDING') {
      throwHttp(ctx, 409, '申请已被处理，请刷新后查看');
    }
    if (request.approverId !== user.id) {
      throwHttp(ctx, 403, '只有被指定的审批人可处理此申请');
    }

    // 2) 行锁员工，保证“员工仍为 PROBATION”与状态更新原子。
    const employee = await empRepo.model.findOne({
      where: { id: request.employeeId },
      lock: transaction.LOCK.UPDATE,
      transaction,
    });
    if (!employee) {
      throwHttp(ctx, 400, '员工档案不存在');
    }
    if (employee.userId === user.id) {
      throwHttp(ctx, 403, '不能审批本人的转正申请');
    }
    if (employee.status !== 'PROBATION') {
      throwHttp(ctx, 409, '员工任职状态已变化，无法处理');
    }

    const isApprove = action === 'APPROVE';

    // 3) 更新申请（通过时同步员工状态）。
    await request.update(
      {
        status: isApprove ? 'APPROVED' : 'REJECTED',
        decidedAt: new Date(),
        decisionComment: comment,
        version: (request.version || 0) + 1,
      },
      { transaction },
    );

    if (isApprove) {
      await employee.update({ status: 'REGULAR' }, { transaction });
    }

    // 4) 追加轨迹。
    await actRepo.create({
      values: {
        requestId: request.id,
        actorUserId: user.id,
        action,
        comment,
        actedAt: new Date(),
      },
      transaction,
    });
  });

  const fresh = await reqRepo.findOne({ filterByTk: requestId });
  ctx.body = fresh ? fresh.toJSON() : null;
  await next();
}

async function approve(ctx, next) {
  await decide(ctx, 'APPROVE', next);
}

async function reject(ctx, next) {
  await decide(ctx, 'REJECT', next);
}

module.exports = { saveDraft, submit, approve, reject };
