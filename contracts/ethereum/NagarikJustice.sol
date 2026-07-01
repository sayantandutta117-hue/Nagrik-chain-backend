// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract NagarikJustice {
    enum CaseState {
        Pending,
        Executed,
        Rejected
    }

    struct ExecutionCase {
        bytes32 caseId;
        bytes32 citizenHash;
        bytes32 triggerHash;
        string workflow;
        CaseState state;
        uint256 createdAt;
        uint256 executedAt;
    }

    address public registrar;
    mapping(bytes32 => ExecutionCase) public cases;

    event CaseRegistered(bytes32 indexed caseId, string workflow, bytes32 indexed citizenHash);
    event CaseExecuted(bytes32 indexed caseId, string workflow);
    event CaseRejected(bytes32 indexed caseId, string reason);

    modifier onlyRegistrar() {
        require(msg.sender == registrar, "not registrar");
        _;
    }

    constructor() {
        registrar = msg.sender;
    }

    function registerCase(
        bytes32 caseId,
        bytes32 citizenHash,
        bytes32 triggerHash,
        string calldata workflow
    ) external onlyRegistrar {
        require(cases[caseId].createdAt == 0, "case exists");
        cases[caseId] = ExecutionCase({
            caseId: caseId,
            citizenHash: citizenHash,
            triggerHash: triggerHash,
            workflow: workflow,
            state: CaseState.Pending,
            createdAt: block.timestamp,
            executedAt: 0
        });
        emit CaseRegistered(caseId, workflow, citizenHash);
    }

    function executeCase(bytes32 caseId, bytes32 verifiedTriggerHash) external onlyRegistrar {
        ExecutionCase storage c = cases[caseId];
        require(c.createdAt != 0, "missing case");
        require(c.state == CaseState.Pending, "not pending");
        require(c.triggerHash == verifiedTriggerHash, "trigger mismatch");
        c.state = CaseState.Executed;
        c.executedAt = block.timestamp;
        emit CaseExecuted(caseId, c.workflow);
    }

    function rejectCase(bytes32 caseId, string calldata reason) external onlyRegistrar {
        ExecutionCase storage c = cases[caseId];
        require(c.createdAt != 0, "missing case");
        require(c.state == CaseState.Pending, "not pending");
        c.state = CaseState.Rejected;
        emit CaseRejected(caseId, reason);
    }
}
