// Exhume armcc5: create a Thumb function at every address in starts.txt, analyse, and write
// Ghidra's decompiled C for each to outdir/<ADDR>.c (a draft for a matcher, never the answer).
//   args: starts.txt outdir
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.lang.*;
import java.io.*;
import java.math.BigInteger;
import java.nio.file.*;

public class DecompileAt extends GhidraScript {
    public void run() throws Exception {
        String[] a = getScriptArgs();
        Register tmode = currentProgram.getRegister("TMode");
        java.util.List<Address> starts = new java.util.ArrayList<>();
        for (String line : Files.readAllLines(Paths.get(a[0]))) {
            if (line.isBlank()) continue;
            Address at = toAddr(Long.parseLong(line.trim(), 16));
            starts.add(at);
            if (tmode != null && getInstructionAt(at) == null) {
                try { currentProgram.getProgramContext().setValue(tmode, at, at, BigInteger.ONE); } catch (Exception e) { }
            }
            disassemble(at);
            if (getFunctionAt(at) == null) createFunction(at, String.format("sub_%08X", at.getOffset()));
        }
        analyzeAll(currentProgram);
        DecompInterface d = new DecompInterface();
        d.openProgram(currentProgram);
        Files.createDirectories(Paths.get(a[1]));
        for (Address at : starts) {
            Function f = getFunctionAt(at);
            if (f == null) continue;
            DecompileResults r = d.decompileFunction(f, 60, monitor);
            String c = r.decompileCompleted() ? r.getDecompiledFunction().getC() : "/* decompile failed: " + r.getErrorMessage() + " */\n";
            Files.writeString(Paths.get(a[1], String.format("%08X.c", at.getOffset())), c);
        }
    }
}
