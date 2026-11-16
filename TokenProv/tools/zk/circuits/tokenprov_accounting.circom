pragma circom 2.2.3;

include "../node_modules/circomlib/circuits/poseidon.circom";
include "../node_modules/circomlib/circuits/bitify.circom";

// This circuit proves only the provider accounting and R_link relations.
// It does not prove that an LLM executed correctly, nor does it attest hardware.
template TokenProvAccountingLink() {
    signal input promptTokens;
    signal input completionTokens;
    signal input promptRate;
    signal input completionRate;
    signal input nonce;

    signal input expectedCharge;
    signal input commitment;
    signal input requestHash;
    signal input receiptRequestHash;

    component promptBits = Num2Bits(32);
    promptBits.in <== promptTokens;
    component completionBits = Num2Bits(32);
    completionBits.in <== completionTokens;
    component promptRateBits = Num2Bits(32);
    promptRateBits.in <== promptRate;
    component completionRateBits = Num2Bits(32);
    completionRateBits.in <== completionRate;

    signal promptCharge;
    signal completionCharge;
    promptCharge <== promptTokens * promptRate;
    completionCharge <== completionTokens * completionRate;
    expectedCharge === promptCharge + completionCharge;
    requestHash === receiptRequestHash;

    component poseidon = Poseidon(5);
    poseidon.inputs[0] <== promptTokens;
    poseidon.inputs[1] <== completionTokens;
    poseidon.inputs[2] <== promptRate;
    poseidon.inputs[3] <== completionRate;
    poseidon.inputs[4] <== nonce;
    commitment === poseidon.out;
}

component main {public [expectedCharge, commitment, requestHash, receiptRequestHash]} = TokenProvAccountingLink();
