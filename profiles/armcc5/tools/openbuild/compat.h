/* compat.h: Arm Compiler 5's extensions spelled for clang (and GCC), force-included into every
   source of the open-toolchain build (openbuild.py). Nothing here changes the armcc build.

   Two armcc-isms cannot be a macro and are rewritten in the build's copy of the sources instead
   (openbuild.py, [openbuild] rewrite): `__packed struct` and `__packed union`, whose attribute
   clang ignores before the keyword (silently, with warnings off, so the struct is unpacked and
   its fields move), and `__asm { ... }` embedded assembler blocks. */
#ifndef EXHUME_COMPAT_H
#define EXHUME_COMPAT_H

/* on a field (`__packed unsigned short t;`), or a typedef'd struct after the rewrite */
#define __packed __attribute__((packed))
#define __weak __attribute__((weak))
#define __declspec(x) __attribute__((x))
#define __irq
#define __softfp
#define __pure __attribute__((const))
#define __value_in_regs
#define __forceinline __inline__ __attribute__((always_inline))
#define __align(n) __attribute__((aligned(n)))

/* intrinsics. A project whose CMSIS headers define these as functions for GCC-compatible
   compilers (clang takes their GCC branch, not __CC_ARM's) passes -DEXHUME_NO_INTRINSICS. */
#ifndef EXHUME_NO_INTRINSICS
#define __nop() __asm volatile("nop")
#define __wfi() __asm volatile("wfi")
#define __wfe() __asm volatile("wfe")
#define __sev() __asm volatile("sev")
#define __dsb(x) __asm volatile("dsb" ::: "memory")
#define __dmb(x) __asm volatile("dmb" ::: "memory")
#define __isb(x) __asm volatile("isb" ::: "memory")
#define __breakpoint(x) __asm volatile("bkpt %0" :: "i"(x))
#define __enable_irq() __asm volatile("cpsie i" ::: "memory")
#define __enable_fiq() __asm volatile("cpsie f" ::: "memory")
#define __disable_fiq() __asm volatile("cpsid f" ::: "memory")
#define __clz(x) __builtin_clz(x)
#define __rev(x) __builtin_bswap32(x)
#define __return_address() __builtin_return_address(0)
#define __current_pc() ({ unsigned int pc_; __asm volatile("mov %0, pc" : "=r"(pc_)); pc_; })
#define __current_sp() ({ unsigned int sp_; __asm volatile("mov %0, sp" : "=r"(sp_)); sp_; })

/* armcc's __disable_irq returns whether interrupts were already masked */
static __inline__ __attribute__((always_inline)) int __exhume_disable_irq(void)
{
    int m;
    __asm volatile("mrs %0, primask\n\tcpsid i" : "=r"(m) :: "memory");
    return m & 1;
}
#define __disable_irq() __exhume_disable_irq()
#endif

#endif
