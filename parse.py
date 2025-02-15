"""Parse the WHO catalogue to GARC for use within piezo

Use any argument to this script to force re-parsing rather than using pickles where available
"""

import copy
import json
import os
import pickle
import re
import sys

import gumpy
import numpy
import pandas as pd
from tqdm import tqdm
import functools
import logging
import tempfile


logging.basicConfig(
    filename="who-parse.log",
    filemode="w",
    format="%(asctime)s -  %(levelname)s - %(message)s",
    datefmt="%d-%b-%y %H:%M:%S",
    level=logging.DEBUG,
)
print = functools.partial(print, flush=True)


def parse_who_catalog(filename):
    """Parses the WHO TB catalog

    Args:
        filename (str): Path to the WHO catalog
    Returns:
        pd.dataframe: Dataframe containing the mutations
    """
    df = pd.read_excel(filename, sheet_name="Genome_indices")
    return df


def rev_comp_snp(reference, gene, pos, ref, alt, masks):
    """Convert a mutation into the appropriate number of SNPs in GARC, converting to amino acids as required

    Args:
        reference (gumpy.Genome): Reference genome object
        gene (str): Gene name
        pos (int): Genome index
        ref (str): Reference base(s)
        alt (str): Mutant base(s)
        masks (dict): Dictionary used for caching the masks required for rebuilding genes
    Returns:
        list(str): List of SNP mutations in GARC
    """
    mutations = []
    ref_seq = reference.nucleotide_sequence.copy()
    offset = 0
    for index, (r, a) in enumerate(zip(ref, alt)):
        if r is None or a is None:
            offset += 1
            continue
        if r is not None and a is not None and r != a:
            if (pos + index) - reference.genes[gene]["start"] < 0:
                # Past the end of the gene so just return
                # print(f"Cut off snp, returning {mutations} from ", gene, pos, ''.join([i for i in ref if i is not None]), ''.join([i for i in alt if i is not None]), reference.genes[gene]["end"], sep="\t")
                return mutations
            if (
                reference.genes[gene]["end"] - (pos + index) <= 0
                or reference.genes[gene]["codes_protein"] == False
            ):
                if reference.genes[gene]["end"] - (pos + index) <= 0:
                    # Promoter
                    p = reference.genes[gene]["end"] - (pos + index)
                    mask = reference.nucleotide_index == pos + index
                else:
                    # Non coding
                    p = reference.genes[gene]["end"] - (pos + index) - 1
                    mask = reference.nucleotide_index == pos + index - 1
                if ref_seq[mask] != r:
                    print(
                        f"Given ref did not match: {ref_seq[mask]} != {r} for {gene} {p} {ref} {alt} {pos+index}"
                    )
                r, a = gumpy.Gene._complement([r, a])
                mutations.append(gene + "@" + r + str(p) + a)
            else:
                if ref_seq[pos + index - 1] != r:
                    print(
                        f"Given ref did not match: {ref_seq[pos + index - 1]} != {r} for {gene} {ref} {alt} {pos+index-1}"
                    )
                ref_seq[pos + index - 1] = a

    if reference.genes[gene]["codes_protein"]:
        stacked_mask, mask = masks[gene]

        ref_gene = reference.build_gene(gene)

        g = gumpy.Gene(
            gene,
            ref_seq[mask],
            reference.nucleotide_index[mask],
            reference.stacked_nucleotide_number[stacked_mask],
            reference.stacked_is_cds[stacked_mask],
            reference.stacked_is_promoter[stacked_mask],
            reference.is_indel[mask],
            reference.indel_length[mask],
            reference.indel_nucleotides[mask],
            reference.genes[gene]["reverse_complement"],
            reference.genes[gene]["codes_protein"],
            reference.genes[gene]["type"],
            [],
            [],
            [False for i in ref_seq[mask]],
            {},
        )

        aa_mut = [
            (i + 1, ref_aa, alt_aa)
            for (i, (ref_aa, alt_aa)) in enumerate(zip(ref_gene.codons, g.codons))
            if ref_aa != alt_aa
        ]
        for pos_, r, a in aa_mut:
            r_ = g.codon_to_amino_acid[r]
            a_ = g.codon_to_amino_acid[a]
            mutations.append(gene + "@" + r_ + str(pos_) + a_)
            # If this was synonymous, add the nucelotide changes too (to account for cases such as fabG1@L203L)
            if r_ == a_:
                for i, (rn, an) in enumerate(zip(r, a)):
                    if rn != an:
                        mutations.append(
                            gene + "@" + rn + str((pos_ - 1) * 3 + i + 1) + an
                        )
    return mutations


def snps(reference, gene, pos, ref, alt, masks):
    """Convert a mutation into the appropriate number of SNPs in GARC, converting to amino acids as required

    Args:
        reference (gumpy.Genome): Reference genome object
        gene (str): Gene name
        pos (int): Genome index
        ref (str): Reference base(s)
        alt (str): Mutant base(s)
        masks (dict): Dictionary used for caching the masks required for rebuilding genes
    Returns:
        list(str): List of SNP mutations in GARC
    """
    mutations = []
    ref_seq = reference.nucleotide_sequence.copy()
    offset = 0
    for index, (r, a) in enumerate(zip(ref, alt)):
        if r is None or a is None:
            offset += 1
            continue
        if r is not None and a is not None and r != a:
            if reference.genes[gene]["end"] - (pos + index) <= 0:
                # Past the end of the gene so just return
                # print(f"Cut off snp, returning {mutations} from ", gene, pos, ''.join([i for i in ref if i is not None]), ''.join([i for i in alt if i is not None]), reference.genes[gene]["end"], sep="\t")
                return mutations
            if (pos + index) - reference.genes[gene]["start"] < 0:
                # Promoter so return the difference in nucleotides
                mask = reference.nucleotide_index == pos + index
                if ref_seq[mask] != r:
                    print(
                        f"Given ref did not match: {ref_seq[mask]} != {r} for {gene} {ref} {alt} {pos+index}"
                    )
                mutations.append(
                    gene
                    + "@"
                    + r
                    + str((pos + index) - reference.genes[gene]["start"])
                    + a
                )
            elif reference.genes[gene]["codes_protein"] == False:
                # Non coding so return the difference in nucleotides adjusting for promoter indexing
                mask = reference.nucleotide_index == pos + index
                if ref_seq[mask] != r:
                    print(
                        f"Given ref did not match: {ref_seq[mask]} != {r} for {gene} {ref} {alt} {pos+index}"
                    )
                mutations.append(
                    gene
                    + "@"
                    + r
                    + str((pos + index) - reference.genes[gene]["start"])
                    + a
                )

            else:
                if ref_seq[pos + index - 1] != r:
                    print(
                        f"Given ref did not match: {ref_seq[pos + index - 1]} != {r} for {gene} {ref} {alt} {pos+index-1}"
                    )
                ref_seq[pos + index - 1] = a
    if reference.genes[gene]["codes_protein"]:
        stacked_mask, mask = masks[gene]

        ref_gene = reference.build_gene(gene)

        g = gumpy.Gene(
            gene,
            ref_seq[mask],
            reference.nucleotide_index[mask],
            reference.stacked_nucleotide_number[stacked_mask],
            reference.stacked_is_cds[stacked_mask],
            reference.stacked_is_promoter[stacked_mask],
            reference.is_indel[mask],
            reference.indel_length[mask],
            reference.indel_nucleotides[mask],
            reference.genes[gene]["reverse_complement"],
            reference.genes[gene]["codes_protein"],
            reference.genes[gene]["type"],
            [],
            [],
            [False for i in ref_seq[mask]],
            {},
        )

        aa_mut = [
            (i + 1, ref_aa, alt_aa)
            for (i, (ref_aa, alt_aa)) in enumerate(zip(ref_gene.codons, g.codons))
            if ref_aa != alt_aa
        ]
        for pos_, r, a in aa_mut:
            r_ = g.codon_to_amino_acid[r]
            a_ = g.codon_to_amino_acid[a]
            mutations.append(gene + "@" + r_ + str(pos_) + a_)
            # If this was synonymous, add the nucelotide changes too (to account for cases such as fabG1@L203L)
            if r_ == a_:
                for i, (rn, an) in enumerate(zip(r, a)):
                    if rn != an:
                        mutations.append(
                            gene + "@" + rn + str((pos_ - 1) * 3 + i + 1) + an
                        )
    return mutations


def del_calls(reference, gene, pos, ref, alt, masks, rev_comp=False):
    """Deal with del calls. Attempts to identify dels mid-sequence.
        If a repeated base is deleted (aaa->aa), it is assumed that the first base is deleted.

    Args:
        reference (gumpy.Genome): Reference genome object
        gene (str): Gene name
        pos (int): Genome position
        ref (list): Reference bases
        alt (list): Alternative bases
        masks (dict): Dictionary of gene_name->(stacked_mask, mask)
        rev_comp (bool, optional): Flag to determine if reverse complement. Defaults to False
    Returns:
        list(str): List of mutations in GARC
    """
    # Del has len(alt) < len(ref)
    del_len = len(ref) - len(alt)
    current = None
    current_snps = 999
    start = 0
    ref1 = list(ref)
    # Iterate through the positions at which the ins could occur, checking which has the lowest overall SNPs
    for x in range(len(alt) + 1):
        alt1 = (
            [alt[i] for i in range(x)]
            + [None for i in range(del_len)]
            + [alt[i] for i in range(x, len(alt))]
        )
        if snp_number(ref1, alt1) <= current_snps:
            current = alt1
            current_snps = snp_number(ref1, alt1)
            start = x
    # Position with the best SNPs is the best position for the ins
    seq = [ref[i] for i in range(len(current)) if current[i] is None]
    if rev_comp:
        p = reference.genes[gene]["end"] - (pos + start)
        r = "".join(gumpy.Gene._complement(seq))
        snp = rev_comp_snp(reference, gene, pos, ref, current, masks)
        if p - 1 > reference.genes[gene]["end"] - reference.genes[gene]["start"]:
            # print(p, pos, start, reference.genes[gene]["end"])
            # Del happened past the 3' end of the gene so ignore it
            # print(f"Cut off del, returning {snp} from ", gene, pos, ''.join([i for i in ref if i is not None]), ''.join([i for i in alt if i is not None]), reference.genes[gene]["end"],  sep="\t")
            return []
    else:
        p = pos - reference.genes[gene]["start"] + start
        r = "".join(seq)
        snp = snps(reference, gene, pos, ref, current, masks)
        if p > reference.genes[gene]["end"] - reference.genes[gene]["start"]:
            # print(p, pos, start, reference.genes[gene]["end"])
            # If the del happened past the 3' end of the gene, ignore it
            # print(f"Cut off del, returning {snp} from ", gene, pos, ''.join([i for i in ref if i is not None]), ''.join([i for i in alt if i is not None]), reference.genes[gene]["end"], sep="\t")
            return []
    # Promoter adjustment to accomodate the -2,-1,1,2 indexing
    if p <= 0:
        p -= 1
    # Rev comp adjustments
    if rev_comp:
        # Rev comp should count from the RHS not the LHS, and should be reversed...
        return snp + [gene + "@" + str(p - len(r) + 1) + "_del_" + r[::-1]]
    else:
        return snp + [gene + "@" + str(p) + "_del_" + r]


def snp_number(ref, alt):
    """Helper function to find the SNP distance between two arrays, ignoring None values

    Args:
        ref (list): List of bases
        alt (list): List of bases

    Returns:
        int: SNP distance ignoring None values
    """
    snps = 0
    for a, b in zip(ref, alt):
        if a is not None and b is not None and a != b:
            snps += 1
    return snps


def ins_calls(reference, gene, pos, ref, alt, masks, rev_comp=False):
    """Deal with ins calls. Attempts to detect mid-sequence insertions.
        If a repeated base has an insertion, it is assumed that the insertion occured at first base (`aa`->`aaa` infers ins @ seq[0])

    Args:
        reference (gumpy.Genome): Reference Genome object
        gene (str): Gene name
        pos (int): Genome index
        ref (str): Reference bases
        alt (str): Alternative bases
        masks (dict): Dictionary of gene_name->(stacked_mask, mask)
        rev_comp (bool, optional): Flag to show if the gene is reverse complement. Defaults to False
    Returns:
        list(str): List of mutations in GARC
    """
    # Ins has len(ref) < len(alt)
    ins_len = len(alt) - len(ref)
    current = None
    # Arbitrarily high SNPs so it can only decrease
    current_snps = 999
    start = 0
    alt1 = list(alt)
    # Iterate through the positions at which the ins could occur, checking which has the lowest overall SNPs
    for x in range(len(ref) + 1):
        ref1 = (
            [ref[i] for i in range(x)]
            + [None for i in range(ins_len)]
            + [ref[i] for i in range(x, len(ref))]
        )
        if snp_number(ref1, alt1) <= current_snps:
            current = ref1
            current_snps = snp_number(ref1, alt1)
            start = x
    # Position with the best SNPs is the best position for the ins
    seq = [alt[i] for i in range(len(current)) if current[i] is None]
    alt1 = [alt[i] for i in range(len(current)) if current[i] is not None]
    if rev_comp:
        p = reference.genes[gene]["end"] - (pos + start)
        a = "".join(gumpy.Gene._complement(seq))
        snp = rev_comp_snp(reference, gene, pos, ref, alt1, masks)
        if p - 1 > reference.genes[gene]["end"] - reference.genes[gene]["start"]:
            # Past the 3' end so ignore
            print(
                f"Cut off ins, returning {snp} from",
                gene,
                pos,
                "".join([i for i in ref if i is not None]),
                "".join([i for i in alt if i is not None]),
                reference.genes[gene]["end"],
                sep="\t",
            )
            return snp
        # -1 if promoter, +1 if not
        if p <= 0:
            p -= 1
        else:
            p += 1
    else:
        p = pos - reference.genes[gene]["start"] + start
        a = "".join(seq)
        snp = snps(reference, gene, pos, ref, alt1, masks)
        if p > reference.genes[gene]["end"] - reference.genes[gene]["start"]:
            # Past the 3' end so ignore
            # print(f"Cut off ins, returning {snp} from", gene, pos, ''.join([i for i in ref if i is not None]), ''.join([i for i in alt if i is not None]), reference.genes[gene]["end"], sep="\t")
            return snp
    # Promoter adjustment to accomodate the -2,-1,1,2 indexing
    if p <= 0:
        p -= 1
    if rev_comp:
        return snp + [gene + "@" + str(p) + "_ins_" + a[::-1]]
    else:
        return snp + [gene + "@" + str(p) + "_ins_" + a]


def to_garc(reference, gene, pos, ref, alt, masks):
    """Convert to GARC

    Args:
        reference (gumpy.Genome): Reference genome object
        pos (int): Genome index
        ref (str): Reference base(s)
        alt (str): Mutant base(s)
        masks (dict): Dictionary of gene_name->(stacked_mask, mask)
    Returns:
        list(str): List of mutations in GARC
    """
    rev_comp = reference.genes[gene]["reverse_complement"]
    if len(ref) == len(alt):
        if rev_comp:
            return rev_comp_snp(reference, gene, pos, ref, alt, masks)
        else:
            return snps(reference, gene, pos, ref, alt, masks)
    elif len(ref) > len(alt):
        """
        Indels are weirder than I first thought, they don't always indicate a del/ins at the end of the seq
            e.g. agctctagtg -> agtctagta has a `c` being deleted mid-seq (seq[2])
        With this kind of mid-seq indel, it is not possible to determine which position an indel occurs at, especially as there are often SNPs too:
            e.g:
                tccggtctg -> a is ambiguous as to which values are delted/SNPs so the positions reported could be wrong
                accg -> a is ambiguous as to if this is del(ccg) or del some other 3 bases and a SNP
        Also, GARC does not have syntax to suport both insertions and deltions simaltaneously
        In order to make some sense of this, the indel is selected based on where in the sequence it causes the least SNPs. If there are
        repeating sequences as detailed above, the first one is selected. This is not a perfect solution, but allows some form of standardisation
        with the least possible mutations from a single row.
        """
        # Del
        return del_calls(reference, gene, pos, ref, alt, masks, rev_comp=rev_comp)
    elif len(ref) < len(alt):
        # Ins
        return ins_calls(reference, gene, pos, ref, alt, masks, rev_comp=rev_comp)
    else:
        # This should never be reached, but if it is, record it
        print("???", gene, pos, ref, alt, sep="\t")


def get_masks(reference, gene):
    """Find the numpy masks for the arrays within the reference genome for the specified gene.
    The masks are used to speed up the instanciation of new Gene objects for SNP finding. Finding the masks
        takes some time, so this is cached so the mask only has to be found once per gene rather than once per row

    Args:
        reference (gumpy.Genome): Reference genome object
        gene (str): Gene name
    Returns:
        (numpy.array, numpy.array): Tuple of 2 numpy arrays. First denotes the stacked mask for mutli-dimensional
                                    attributes. Second denotes the mask for 1D attributes
    """
    # The mask for all stacked arrays (N-dim)
    stacked_mask = reference.stacked_gene_name == gene
    # The mask for singular arrays (1-dim) by collapsing stacked mask to 1-dim
    mask = numpy.any(stacked_mask, axis=0)
    return stacked_mask, mask


def addMetadata() -> None:
    """Add metadata from the other page of the catalogue to each row of the parsed catalogue"""
    # Get the mapping of GARC values to a variant value (from the catalogue)
    garcToVariant = pickle.load(open("garcVariantMap.pkl", "rb"))

    # Load the GARC catalogue
    catalogue = pd.read_csv("WHO-UCN-GTB-PCI-2021.7.GARC.csv")

    # Load the WHO values for metadata
    values = pd.read_excel(
        "WHO-UCN-GTB-PCI-2021.7-eng.xlsx", sheet_name="Mutation_catalogue"
    )

    # Add the common names as these may be different names for the same nucleotide mutation
    fixed = {name: [] for name in values.columns}
    for _, row in values.iterrows():
        row = row.to_dict()
        variant = row["variant (common_name)"]
        # Quick check due to some NAN values
        if isinstance(variant, str):
            variants = variant.split(" ")
            # For each name this variant has, add a row...
            for var in variants:
                # Change the variant name
                row["variant (common_name)"] = var.strip()
                # Add everything from this row
                for key, val in row.items():
                    fixed[key].append(val)
        else:
            for key, val in row.items():
                fixed[key].append(val)
    values = pd.DataFrame.from_dict(fixed)

    # Iter the GARC catalogue, match the rows of values based on the variant and drug
    evidences = []
    others = []
    toDrop = []
    for i, row in list(catalogue.iterrows()):
        # Detect generic rules and skip these as they do not have associated evidence
        generic = re.compile(
            r"""
                            ([a-zA-Z_0-9]+@) #Leading gene name
                            ((-?\*\?)|(\*=)|(-?\*_indel))
                            """,
            re.VERBOSE,
        )
        if generic.fullmatch(row["MUTATION"]):
            evidences.append(json.dumps({}))
            others.append(json.dumps({}))
            continue

        drug = row["DRUG"]
        garc = row["MUTATION"]
        prediction = row["PREDICTION"]

        try:
            variant = garcToVariant[(garc, drug, prediction)]
        except KeyError:
            # Could be this
            if drug == "LEV" and "gyr" in garc:
                # This is an LEV row added because of an expert rule, so pull out the metadata for the original row
                drug = "MXF"
                variant = garcToVariant[(garc, drug, prediction)]

        vals = values.loc[
            (values["variant (common_name)"] == variant) & (values["drug"] == drug)
        ]

        if "LoF" in vals["Additional grading criteria"]:
            if "del" not in garc and "!" not in garc and "fs" not in garc:
                # print("Described variant isn't in ref/alt pair: ", variant, garc, sep="\t")
                toDrop.append(i)
        elif "del" in variant:
            if "del" not in garc:
                # print("Described variant isn't in ref/alt pair: ", variant, garc, sep="\t")
                toDrop.append(i)
        elif "ins" in variant:
            if "ins" not in garc:
                # print("Described variant isn't in ref/alt pair: ", variant, garc, sep="\t")
                toDrop.append(i)
        elif "fs" in variant:
            if "ins" not in garc and "del" not in garc and "fs" not in garc:
                # print("Described variant isn't in ref/alt pair: ", variant, garc, sep="\t")
                toDrop.append(i)

        if len(vals) == 0:
            # No records found, probably due to a synonymous mutation
            evidences.append(json.dumps({}))
            others.append(json.dumps({}))
            continue

        # Because the catalogue uses merged cells, the column names are not consistent. Equivalent to:
        evidenceNames = [
            "Present_SOLO_R",
            "Present_SOLO_SR",
            "Present_S",
            "Absent_S",
            "Present_R",
            "Absent_R",
        ]
        evidenceFields = [
            "Unnamed: 5",
            "Unnamed: 6",
            "Unnamed: 7",
            "Unnamed: 8",
            "Unnamed: 9",
            "Unnamed: 10",
        ]
        evidenceNames = dict(zip(evidenceFields, evidenceNames))

        evidences.append(
            json.dumps(
                {
                    evidenceNames[field]: vals[field].values[0]
                    for field in evidenceFields
                }
            )
        )

        others.append(
            json.dumps(
                {"FINAL_CONFIDENCE_GRADING": vals["FINAL CONFIDENCE GRADING"].values[0]}
            )
        )

    catalogue["EVIDENCE"] = evidences
    catalogue["OTHER"] = others
    catalogue.drop(toDrop, axis=0, inplace=True)
    catalogue.to_csv("WHO-UCN-GTB-PCI-2021.7.GARC.csv", index=False)


def addExpertRules() -> None:
    """Add expert rules which are separate from the WHO catalogue.
    These are stored in a csv of the same format, so just concat
    """
    catalogue = pd.read_csv("WHO-UCN-GTB-PCI-2021.7.GARC.csv")
    expert = pd.read_csv("expertRules.csv")
    result = pd.concat([catalogue, expert])
    logging.info("Concated the expert rules")
    result.to_csv("WHO-UCN-GTB-PCI-2021.7.GARC.csv", index=False)


def build_vcf(ref: str, alt: str, pos: int) -> gumpy.VCFFile:
    """Parse the ref/alt from the row and build a VCF object for it

    Args:
        row (pd.Series): Row to build a variant from

    Returns:
        gumpy.VCFFile: VCF file object resulting
    """
    vcf = f"""##fileformat=VCFv4.2
##source=minos, version 0.12.5
##fileDate=2023-10-28
##FORMAT=<ID=ALLELE_DP,Number=R,Type=Float,Description="Mean read depth of ref and each allele">
##FORMAT=<ID=COV,Number=R,Type=Integer,Description="Number of reads on ref and alt alleles">
##FORMAT=<ID=COV_TOTAL,Number=1,Type=Integer,Description="Total reads mapped at this site, from gramtools">
##FORMAT=<ID=DP,Number=1,Type=Float,Description="Mean read depth of called allele (ie the ALLELE_DP of the called allele)">
##FORMAT=<ID=FRS,Number=1,Type=Float,Description="Fraction of reads that support the genotype call">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##FORMAT=<ID=GT_CONF,Number=1,Type=Float,Description="Genotype confidence. Difference in log likelihood of most likely and next most likely genotype">
##minosMeanReadDepth=42.379
##minosReadDepthVariance=937.254
##contig=<ID=NC_000962.3,length=4411532>
##FORMAT=<ID=GT_CONF_PERCENTILE,Number=1,Type=Float,Description="Percentile of GT_CONF">
##FILTER=<ID=MIN_FRS,Description="Minimum FRS of 0.9">
##FILTER=<ID=MIN_DP,Description="Minimum DP of 2">
##FILTER=<ID=MIN_GCP,Description="Minimum GT_CONF_PERCENTILE of 0.5">
##FILTER=<ID=MAX_DP,Description="Maximum DP of 134.22281307415324 (= 3.0 standard deviations from the mean read depth 42.379)">
#CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO	FORMAT	sample
NC_000962.3	{pos}	.	{ref}	{alt}	.	PASS	.	GT:DP:ALLELE_DP:FRS:COV_TOTAL:COV:GT_CONF:GT_CONF_PERCENTILE	1/1:94:0,94:1.0:94:0,94:590.62:92.99"""
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write(vcf)
    v = gumpy.VCFFile(str(f.name))
    return v


def parse_ref_alt(
    reference: gumpy.Genome,
    ref_genes: dict[str, gumpy.Gene],
    ref: str,
    alt: str,
    pos: int,
) -> list[str]:
    """Use the ref/alt/pos to pull out the variants via VCF

    Args:
        reference (gumpy.Genome): Reference genome
        ref_genes (dict[str, gumpy.Gene]): Reference genes
        row (pd.Series): Row of the catalogue

    Returns:
        list[str]: List of mutations in GARC originating from this row
    """

    # Find out which genes this affects
    genes = set()
    for idx, r in enumerate(ref):
        mask = reference.stacked_nucleotide_index == pos + idx
        g = reference.stacked_gene_name[mask]
        for g_ in g:
            genes.add(g_)

    vcf = build_vcf(ref, alt, pos)
    sample = reference + vcf
    garc = []
    for gene in sorted(list(genes)):
        if gene:
            ref_gene = ref_genes[gene]
            alt_gene = sample.build_gene(gene)
            diff = ref_gene - alt_gene
            muts = diff.mutations.tolist()
            for mut in muts:
                garc.append(gene + "@" + mut)
    return garc


def parse(reference: gumpy.Genome, data: pd.DataFrame) -> dict:
    """Parse the catalogue. Takes a long time due to gene rebuilding (25-40 mins)...
    Dumps the output to a pickle, along with a map for rows to the `variants` column of the catalogue


    Args:
        reference (gumpy.Genome): Reference genome
        data (pd.DataFrame): Loaded WHO catalogue

    Returns:
        dict: Dictionary of {drug: {'R': {mutations}, 'U': {mutations}, 'S': {mutations}}}
    """
    # The catalogue is grouped by gene, so we can store gene masks until they are no longer required
    masks = {}

    # Setup details for drugs
    drug_columns = [
        "RIF_Conf_Grade",
        "INH_Conf_Grade",
        "EMB_Conf_Grade",
        "PZA_Conf_Grade",
        "LEV_Conf_Grade",
        "MXF_Conf_Grade",
        "BDQ_Conf_Grade",
        "LZD_Conf_Grade",
        "CFZ_Conf_Grade",
        "DLM_Conf_Grade",
        "AMI_Conf_Grade",
        "STM_Conf_Grade",
        "ETH_Conf_Grade",
        "KAN_Conf_Grade",
        "CAP_Conf_Grade",
    ]
    drugs = {
        drug.split("_")[0]: {
            "R": set(),
            "U": set(),
            "S": set(),
            # "F": set()
        }
        for drug in drug_columns
    }
    genes = set()
    garcToVariant = {}  # Mapping of (GARC, drug, prediction) --> variant

    ref_genes = {}
    print("Building ref genes...")
    for gene in tqdm(reference.genes):
        ref_genes[gene] = reference.build_gene(gene)

    # Iterate over the catalogue
    for index, row in tqdm([x for x in data.iterrows()]):
        garc = []
        # Pull out gene name, pos, ref and alt
        gene = row["gene_name"]
        genes.add(gene)
        # if masks.get(gene) is None:
        #     #Cache the masks
        #     masks = {gene: get_masks(reference, gene)}
        pos = str(row["final_annotation.Position"])  # Cast to a str for str.split(',')
        ref = row["final_annotation.ReferenceNucleotide"]
        alt = row["final_annotation.AlternativeNucleotide"]

        # Check for multiple positions defined within pos
        if len(pos.split(",")) > 1:
            # There is more than 1 mutation detailed in this row, so skip it
            print("Mulitple mutations per row: ", gene, pos, ref, alt, sep="\t")
            continue
        else:
            garc = parse_ref_alt(reference, ref_genes, ref, alt, int(pos))
            # garc += to_garc(reference, gene, int(pos), ref, alt, masks)
            if len(garc) > 1:
                # There is more than 1 mutation generated from this row, so skip it
                # print("Multiple mutations per row: ", gene, pos, ref, alt, garc, sep="\t")
                # continue
                garc = ["&".join(sorted(garc))]

        for drug in drug_columns:
            col = row[drug]
            drug = drug.split("_")[0]
            category = None
            if pd.isnull(col):
                continue
            if "1)" in col or "2)" in col:
                # Resistance
                category = "R"
            elif "3)" in col:
                # Uncertain
                category = "U"
            elif "4)" in col or "5)" in col or col == "Synonymous":
                # Not resistant
                category = "S"

            for mutation in garc:
                drugs[drug][category].add(mutation)
                garcToVariant[(mutation, drug, category)] = row["variant"]

    # Dump for easier testing
    pickle.dump(drugs, open("drugs.pkl", "wb"))
    pickle.dump(garcToVariant, open("garcVariantMap.pkl", "wb"))
    return drugs


def addExtras(reference: gumpy.Genome) -> None:
    """Once the catalogue has been parsed correctly, there will be some mutations which also lie within other genes
    This finds them and adds them to the catalogue. Specifically, this checks for promoter SNPs which could be attributed
    to other genes, especially in cases where the promoter position is beyond the arbitrary internal limits of gumpy.
    As this utilises gumpy, it is quite slow (~20 mins), but it reliably produces results.
    Args:
        reference (gumpy.Genome): Reference genome
    """
    catalogue = pd.read_csv("WHO-UCN-GTB-PCI-2021.7.GARC.csv")
    toAdd = {column: [] for column in catalogue}

    # Track the new resistance genes this introduces to add default rules
    newGenes = set()
    previousGenes = set([mutation.split("@")[0] for mutation in catalogue["MUTATION"]])
    for _, row in catalogue.iterrows():
        mut = row["MUTATION"]
        # Check for promoter
        if "-" not in mut:
            continue
        # Check for default rules/multi for skipping
        if "*" in mut or "?" in mut or "&" in mut or "indel" in mut:
            continue
        promoterSNP = re.compile(
            r"""
                            ([a-zA-Z0-9_]+)@ #Leading gene name
                            ([a-z])(-[0-9]+)([a-z]) #Nucleotide SNP
                            """,
            re.VERBOSE,
        )
        # With indels, we need the bases to properly pull out other mutations
        # So we have to ignore cases where bases are ambiguous or just a number
        promoterIns = re.compile(
            r"""
                            ([a-zA-Z0-9_]+)@ #Leading gene name
                            (-[0-9]+)_ins_([acgt]+)
                            """,
            re.VERBOSE,
        )
        promoterDel = re.compile(
            r"""
                            ([a-zA-Z0-9_]+)@ #Leading gene name
                            (-[0-9]+)_del_([acgt]+)
                            """,
            re.VERBOSE,
        )
        if promoterSNP.fullmatch(mut):
            gene, ref, pos, alt = promoterSNP.fullmatch(mut).groups()
            pos = int(pos)
            sample = copy.deepcopy(reference)
            # print(gene, pos, ref, alt)

            # Place the mutation within the genome based on the gene coordinates
            # Then regardless of what gene it started in, we can pull out others
            if reference.genes[gene]["reverse_complement"]:
                # Revcomp genes' promoters will be past the `gene end`
                geneEnd = reference.genes[gene]["end"]
                ref_ = "".join(gumpy.Gene._complement(ref))
                alt_ = "".join(gumpy.Gene._complement(alt))
                pos_ = geneEnd - pos - 1
                assert (
                    reference.nucleotide_sequence[
                        reference.nucleotide_index == geneEnd - pos
                    ]
                    == ref_
                ), "Ref does not match the genome..."
                sample.nucleotide_sequence[
                    reference.nucleotide_index == geneEnd - pos
                ] = alt_
            else:
                geneStart = reference.genes[gene]["start"]
                pos_ = geneStart + pos
                assert (
                    reference.nucleotide_sequence[
                        reference.nucleotide_index == geneStart + pos
                    ]
                    == ref
                ), "Ref does not match the genome..."
                sample.nucleotide_sequence[
                    reference.nucleotide_index == geneStart + pos
                ] = alt

        elif promoterIns.fullmatch(mut):
            gene, pos, bases = promoterIns.fullmatch(mut).groups()
            sample = copy.deepcopy(reference)
            pos = int(pos)
            # print(gene, pos, "ins", bases)

            if reference.genes[gene]["reverse_complement"]:
                # Revcomp genes' promoters will be past the `gene end`
                geneEnd = reference.genes[gene]["end"]
                bases = "".join(gumpy.Gene._complement(bases)[::-1])
                pos_ = geneEnd - pos
            else:
                geneStart = reference.genes[gene]["start"]
                pos_ = geneStart + pos

            sample.is_indel[pos_] = True
            sample.indel_length[pos_] = len(bases)
            sample.indel_nucleotides[pos_] = bases

        elif promoterDel.fullmatch(mut):
            gene, pos, bases = promoterDel.fullmatch(mut).groups()
            sample = copy.deepcopy(reference)
            pos = int(pos)
            # print(gene, pos, "del", bases)

            if reference.genes[gene]["reverse_complement"]:
                # Revcomp genes' promoters will be past the `gene end`
                geneEnd = reference.genes[gene]["end"]
                bases = "".join(gumpy.Gene._complement(bases)[::-1])
                # Pos is a little more involved here
                # Should be adjusted by the length of the bases too
                pos_ = geneEnd - pos - 1 - len(bases)
            else:
                geneStart = reference.genes[gene]["start"]
                pos_ = geneStart + pos

            sample.is_indel[pos_] = True
            sample.indel_length[pos_] = -len(bases)
            sample.indel_nucleotides[pos_] = bases

        else:
            # No matches so skip it
            continue

        # The only mutations between ref and sample are the promoter mutation introduced above
        # So pull out all available mutations (ignoring the original gene)
        mutations = []
        # Get genes at this position
        possible = [
            reference.stacked_gene_name[i][pos_]
            for i in range(len(reference.stacked_gene_name))
            if reference.stacked_gene_name[i][pos_] != ""
        ]
        for g in possible:
            if g == gene:
                continue
            if g not in previousGenes:
                newGenes.add((g, row["DRUG"]))
            # print("Checking ", g)
            gMutations = []
            diff = reference.build_gene(g) - sample.build_gene(g)
            m = diff.mutations
            for mut_ in m:
                gMutations.append(g + "@" + mut_)
            if gMutations:
                logging.info(
                    f"Found alternative form of {mut}: {'&'.join(sorted(gMutations))}"
                )
            mutations.append("&".join(sorted(gMutations)))
        print()

        # Make them neat catalouge rows to add
        for m in mutations:
            for col in catalogue:
                if col == "MUTATION":
                    toAdd[col].append(m)
                else:
                    toAdd[col].append(row[col])
    rmCol = None
    for key in toAdd.keys():
        if len(toAdd[key]) == 0:
            # Drop a weird empty column if exists
            rmCol = key
    if rmCol is not None:
        del toAdd[rmCol]

    toAdd = pd.DataFrame(toAdd)
    catalogue = pd.concat([catalogue, toAdd])
    logging.info("Concated new versions with the catalogue")

    catalogue.to_csv("WHO-UCN-GTB-PCI-2021.7.GARC.csv", index=False)


def addDefaults(reference: gumpy.Genome):
    """Add in default rules for all resistance genes within the catalogue

    Args:
        reference (gumpy.Genome): Reference genome
    """
    catalogue = pd.read_csv("WHO-UCN-GTB-PCI-2021.7.GARC.csv")
    resistanceGenes = set()

    # Find all of the genes which confer resistance to a given drug
    for i, row in catalogue.iterrows():
        prediction = row["PREDICTION"]
        mutation = row["MUTATION"]
        drug = row["DRUG"]
        if prediction == "R":
            resistanceGenes.add((mutation.split("@")[0], drug))

    toAdd = {column: [] for column in catalogue}
    # Now we know which genes confer resistance, add default rules
    for gene, drug in sorted(list(resistanceGenes)):
        defaults = [
            (gene + "@*?", "U"),
            (gene + "@-*?", "U"),
            (gene + "@*_indel", "U"),
            (gene + "@-*_indel", "U"),
            (gene + "@del_0.0", "U"),
        ]
        if reference.genes[gene]["codes_protein"]:
            defaults.append((gene + "@*=", "S"))
        for rule, pred in defaults:
            toAdd["GENBANK_REFERENCE"].append("NC_000962.3")
            toAdd["CATALOGUE_NAME"].append("WHO-UCN-GTB-PCI-2021.7")
            toAdd["CATALOGUE_VERSION"].append("1.0")
            toAdd["CATALOGUE_GRAMMAR"].append("GARC1")
            toAdd["PREDICTION_VALUES"].append("RUS")
            toAdd["DRUG"].append(drug)
            toAdd["MUTATION"].append(rule)
            toAdd["PREDICTION"].append(pred)
            toAdd["SOURCE"].append("{}")
            toAdd["EVIDENCE"].append("{}")
            toAdd["OTHER"].append("{}")

    rmCol = None
    for key in toAdd.keys():
        if len(toAdd[key]) == 0:
            # Drop a weird empty column if exists
            rmCol = key
    if rmCol is not None:
        del toAdd[rmCol]

    # Now concat the tables and write
    toAdd = pd.DataFrame(toAdd)
    catalogue = pd.concat([catalogue, toAdd])
    catalogue.to_csv("WHO-UCN-GTB-PCI-2021.7.GARC.csv", index=False)


def filterRules() -> None:
    """Filter out mutations which are already covered by default rules"""
    catalogue = pd.read_csv("WHO-UCN-GTB-PCI-2021.7.GARC.csv")
    resistanceGenes = set()

    # Find all of the genes which confer resistance to a given drug
    for i, row in catalogue.iterrows():
        prediction = row["PREDICTION"]
        mutation = row["MUTATION"]
        drug = row["DRUG"]
        if prediction == "R":
            resistanceGenes.add((mutation.split("@")[0], drug))

    fixed = {col: [] for col in catalogue}
    for i, row in catalogue.iterrows():
        toDelete = False
        prediction = row["PREDICTION"]
        mutation = row["MUTATION"]
        if (mutation.split("@")[0], row["DRUG"]) not in resistanceGenes:
            toDelete = True
            # logging.info(f"Removing {row['MUTATION']}:{row['DRUG']}:{row['PREDICTION']} as it is not a resistance gene")
        elif prediction == "U":
            indel = re.compile(
                r"""
                            ([a-zA-Z_0-9]+@) #Leading gene name
                            (
                                (-?[0-9]+_((ins)|(del))_[acgotxz]*) #indel
                            )
                            """,
                re.VERBOSE,
            )
            if indel.fullmatch(mutation):
                # Matched an indel generic so delete
                toDelete = True
                # print("Deleting: ", row['MUTATION'], row['DRUG'])
                logging.info(
                    f"Removing {row['MUTATION']}:{row['DRUG']}:{row['PREDICTION']} as it matches *_indel-->U"
                )

            nonsynon = re.compile(
                r"""
                                ([a-zA-Z_0-9]+@) #Leading gene name
                                (([!ACDEFGHIKLMNOPQRSTVWXYZacgotxz])-?[0-9]+([!ACDEFGHIKLMNOPQRSTVWXYZacgotxz])) #SNP
                                """,
                re.VERBOSE,
            )
            if nonsynon.fullmatch(mutation):
                _, _, base1, base2 = nonsynon.fullmatch(mutation).groups()
                if base1 != base2:
                    # This matches the gene@*? or gene@-*? so delete
                    toDelete = True
                    # print("Deleting: ", row['MUTATION'], row['DRUG'])
                    logging.info(
                        f"Removing {row['MUTATION']}:{row['DRUG']}:{row['PREDICTION']} as it matches *?-->U or -*?-->U"
                    )

        elif prediction == "S":
            # Checking for gene@*=
            # This has now become gene@A*A(&gene@<nucleotide><pos><nucleotide>){1,3}
            synon = re.compile(
                r"""
                                ([a-zA-Z_0-9]+@) #Leading gene name
                                (([!ACDEFGHIKLMNOPQRSTVWXYZ])[0-9]+([!ACDEFGHIKLMNOPQRSTVWXYZ])) #SNP
                                (& #And the nucleotide(s) causing this
                                ([a-zA-Z_0-9]+@) #Gene
                                ([a-z][0-9]+[a-z]))+ #Nucleotide SNP
                                """,
                re.VERBOSE,
            )
            if synon.fullmatch(mutation):
                matches = synon.fullmatch(mutation).groups()
                base1 = matches[2]
                base2 = matches[3]
                if base1 == base2:
                    # Matches the synonymous mutation so delete
                    toDelete = True
                    # print("Deleting: ", row['MUTATION'], row['DRUG'])
                    logging.info(
                        f"Removing {row['MUTATION']}:{row['DRUG']}:{row['PREDICTION']} as it matches *=-->S"
                    )

        if not toDelete:
            # We want to keep this one, so add to fixed
            for col in row.axes[0]:
                fixed[col].append(row[col])

    catalogue = pd.DataFrame(fixed)

    catalogue.to_csv("WHO-UCN-GTB-PCI-2021.7.GARC.csv", index=False)


if __name__ == "__main__":
    # Use any argument to this to force re-parsing rather than using pickles

    # Load the reference genome
    if os.path.exists("reference.pkl"):
        # If the pickled genome exists, use it
        print("Found pickled reference genome")
        reference = pickle.load(open("reference.pkl", "rb"))
        logging.info("Using pickeled reference")
    else:
        # Else load from scratch
        print("Loading reference genome")
        reference = gumpy.Genome("NC_000962.3.gbk", show_progress_bar=True)
        logging.info("Loaded reference from gbk")

    # Load the catalogue
    data = parse_who_catalog("WHO-UCN-GTB-PCI-2021.7-eng.xlsx")

    # If the pickles already exist, use them
    if (
        os.path.exists("drugs.pkl")
        and os.path.exists("garcVariantMap.pkl")
        and len(sys.argv) == 1
    ):
        print("Found pickles, writing the output catalogue")
        drugs = pickle.load(open("drugs.pkl", "rb"))
        logging.info("Using pickled drugs")
    # Else, load
    else:
        print("No pickles found, re-parsing")
        drugs = parse(reference, data)
        logging.info("Re-parsed drugs")

    # Find the genes associated with specific drug resistance
    resistanceGenes = {drug: set() for drug in drugs.keys()}
    for drug in drugs.keys():
        for mutation in drugs[drug]["R"]:
            # These are just resistance mutations, so pull out gene names
            resistanceGenes[drug].add(mutation.split("@")[0])

    logging.info("Found some resistance genes:")
    for gene in sorted(list(resistanceGenes)):
        logging.info(gene)
    logging.info("")

    with open("WHO-UCN-GTB-PCI-2021.7.GARC.csv", "w") as f:
        HEADER = "GENBANK_REFERENCE,CATALOGUE_NAME,CATALOGUE_VERSION,CATALOGUE_GRAMMAR,PREDICTION_VALUES,DRUG,MUTATION,PREDICTION,SOURCE,EVIDENCE,OTHER\n"
        COMMON_ALL = "NC_000962.3,WHO-UCN-GTB-PCI-2021.7,1.0,GARC1,RUS,"
        f.write(HEADER)
        for drug in drugs.keys():
            common = COMMON_ALL + drug + ","
            if resistanceGenes[drug]:

                for category in sorted(list(drugs[drug].keys())):
                    for mutation in sorted(list(drugs[drug][category])):
                        if mutation.split("@")[0] in resistanceGenes[drug]:
                            # Ignore mutations which are already covered by the generic rules
                            if category == "U":
                                # Checking for (non-promoter) indels
                                indel = re.compile(
                                    r"""
                                                    ([a-zA-Z_0-9]+@) #Leading gene name
                                                    (
                                                        ([0-9]+_((ins)|(del))_[acgotxz]*) #indel
                                                    )
                                                    """,
                                    re.VERBOSE,
                                )
                                if indel.fullmatch(mutation):
                                    # Matched an indel generic so skip
                                    logging.info(
                                        f"Skipped {mutation}:{category}:{drug} due to default rule *_indel-->U"
                                    )
                                    continue
                                # Checking for nonsynonymous (non promoter) SNPs
                                # Promoters here can go far beyond the -100 limit in gumpy
                                # So filter out those after adding extras
                                nonsynon = re.compile(
                                    r"""
                                                    ([a-zA-Z_0-9]+@) #Leading gene name
                                                    (([!ACDEFGHIKLMNOPQRSTVWXYZacgotxz])[0-9]+([!ACDEFGHIKLMNOPQRSTVWXYZacgotxz])) #SNP
                                                    """,
                                    re.VERBOSE,
                                )
                                if nonsynon.fullmatch(mutation):
                                    name, mut, base1, base2 = nonsynon.fullmatch(
                                        mutation
                                    ).groups()
                                    if base1 != base2:
                                        # This matches the gene@*? so skip
                                        logging.info(
                                            f"Skipped {mutation}:{category}:{drug} due to default rule *?-->U"
                                        )
                                        continue

                            if category == "S":
                                # Checking for gene@*=
                                # This has now become gene@A*A(&gene@<nucleotide><pos><nucleotide>){1,3}
                                synon = re.compile(
                                    r"""
                                                    ([a-zA-Z_0-9]+@) #Leading gene name
                                                    (([!ACDEFGHIKLMNOPQRSTVWXYZ])[0-9]+([!ACDEFGHIKLMNOPQRSTVWXYZ])) #SNP
                                                    (& #And the nucleotide(s) causing this
                                                    ([a-zA-Z_0-9]+@) #Gene
                                                    ([a-z][0-9]+[a-z]))+ #Nucleotide SNP
                                                    """,
                                    re.VERBOSE,
                                )
                                if synon.fullmatch(mutation):
                                    matches = synon.fullmatch(mutation).groups()
                                    base1 = matches[2]
                                    base2 = matches[3]
                                    if base1 == base2:
                                        # Matches the synonymous mutation so skip
                                        logging.info(
                                            f"Skipped {mutation}:{category}:{drug} due to default rule *=-->S"
                                        )
                                        continue

                            # Helpful mutation, so add it
                            f.write(common + mutation + "," + category + ",{},{},{}\n")
                            logging.info(
                                f"Wrote a useful row: {mutation}:{category}:{drug}"
                            )

                            # Check for an expert rule that gyrA/B@* --> MXF resistance = gyrA/B@* --> LEV resistance and vice versa
                            if drug == "MXF" and category == "R":
                                expert = re.compile(
                                    r"""
                                                    gyr[AB] #Leading gene name
                                                    @(.+) #Any mutation
                                                    """,
                                    re.VERBOSE,
                                )
                                if expert.fullmatch(mutation):
                                    # Match so add the LEV resistance
                                    f.write(
                                        COMMON_ALL
                                        + "LEV,"
                                        + mutation
                                        + ","
                                        + category
                                        + ",{},{},{}\n"
                                    )
                                    logging.info(
                                        f"Wrote a useful row: {mutation}:{category}:{drug}"
                                    )
                            if drug == "LEV" and category == "R":
                                expert = re.compile(
                                    r"""
                                                    gyr[AB] #Leading gene name
                                                    @(.+) #Any mutation
                                                    """,
                                    re.VERBOSE,
                                )
                                if expert.fullmatch(mutation):
                                    # Match so add the LEV resistance
                                    f.write(
                                        COMMON_ALL
                                        + "MXF,"
                                        + mutation
                                        + ","
                                        + category
                                        + ",{},{},{}\n"
                                    )
                                    logging.info(
                                        f"Wrote a useful row: {mutation}:{category}:{drug}"
                                    )

    # Add the evidence JSON
    addMetadata()

    # Add expert rules
    addExpertRules()

    # Add the extras for cases where genes overlap at mutations
    # addExtras(reference)

    # Add the default rules for all resistance genes
    addDefaults(reference)

    # Filter out based on default rules now the extras have been added
    filterRules()
