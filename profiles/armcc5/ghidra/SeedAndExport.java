// Exhume armcc5: seed Thumb functions at the given addresses, analyse, and export every
// function's entry and body ranges.
//   args: seeds.txt out.tsv      (seeds.txt: one hex address per line)
//   out.tsv: entry <TAB> range start <TAB> range end (inclusive), one line per body range
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.lang.*;
import java.io.*;
import java.math.BigInteger;
import java.nio.file.*;

public class SeedAndExport extends GhidraScript {
    public void run() throws Exception {
        String[] a = getScriptArgs();
        Register tmode = currentProgram.getRegister("TMode");
        for (String line : Files.readAllLines(Paths.get(a[0]))) {
            if (line.isBlank()) continue;
            Address at = toAddr(Long.parseLong(line.trim(), 16));
            if (tmode != null && getInstructionAt(at) == null) {
                try { currentProgram.getProgramContext().setValue(tmode, at, at, BigInteger.ONE); }
                catch (Exception e) { }
            }
            disassemble(at);
            if (getFunctionAt(at) == null) createFunction(at, null);
        }
        analyzeAll(currentProgram);
        try (PrintWriter w = new PrintWriter(new FileWriter(a[1]))) {
            for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
                for (AddressRange r : f.getBody()) {
                    w.printf("%08X\t%08X\t%08X%n", f.getEntryPoint().getOffset(),
                             r.getMinAddress().getOffset(), r.getMaxAddress().getOffset());
                }
            }
        }
    }
}
