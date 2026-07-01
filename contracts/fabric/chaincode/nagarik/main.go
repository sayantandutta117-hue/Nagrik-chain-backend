package main

import (
	"encoding/json"
	"fmt"

	"github.com/hyperledger/fabric-contract-api-go/contractapi"
)

type SmartContract struct {
	contractapi.Contract
}

type AuditEvent struct {
	ID          string `json:"id"`
	Actor       string `json:"actor"`
	Action      string `json:"action"`
	SubjectID   string `json:"subject_id"`
	PayloadHash string `json:"payload_hash"`
	CreatedAt   string `json:"created_at"`
}

func (s *SmartContract) RecordAudit(ctx contractapi.TransactionContextInterface, id string, actor string, action string, subjectID string, payloadHash string, createdAt string) (string, error) {
	exists, err := s.EventExists(ctx, id)
	if err != nil {
		return "", err
	}
	if exists {
		return "", fmt.Errorf("audit event %s already exists", id)
	}
	event := AuditEvent{ID: id, Actor: actor, Action: action, SubjectID: subjectID, PayloadHash: payloadHash, CreatedAt: createdAt}
	payload, err := json.Marshal(event)
	if err != nil {
		return "", err
	}
	if err := ctx.GetStub().PutState(id, payload); err != nil {
		return "", err
	}
	return ctx.GetStub().GetTxID(), nil
}

func (s *SmartContract) ReadAudit(ctx contractapi.TransactionContextInterface, id string) (*AuditEvent, error) {
	payload, err := ctx.GetStub().GetState(id)
	if err != nil {
		return nil, err
	}
	if payload == nil {
		return nil, fmt.Errorf("audit event %s does not exist", id)
	}
	var event AuditEvent
	if err := json.Unmarshal(payload, &event); err != nil {
		return nil, err
	}
	return &event, nil
}

func (s *SmartContract) EventExists(ctx contractapi.TransactionContextInterface, id string) (bool, error) {
	payload, err := ctx.GetStub().GetState(id)
	if err != nil {
		return false, err
	}
	return payload != nil, nil
}

func main() {
	chaincode, err := contractapi.NewChaincode(&SmartContract{})
	if err != nil {
		panic(err.Error())
	}
	if err := chaincode.Start(); err != nil {
		panic(err.Error())
	}
}
